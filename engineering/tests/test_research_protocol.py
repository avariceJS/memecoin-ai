"""Cortex preregistration is metadata-only and reuses reviewed A10 transitions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest

from engineering import supervisor
from engineering.contract_review_cycle import process_pending_reviews
from engineering.git_gate import GitGate
from engineering.org_state import StateError, Store
from engineering.research_protocol import (
    CONTRACT_NAME,
    ResearchProtocolError,
    canonical_protocol,
    protocol_sha256,
)
from engineering.tests.test_task_spec_store import (
    ADMISSION,
    BASE,
    POLICY,
    SCOPE,
    SPEC,
)
from engineering.verification_plan import prepare_verification_plan


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


def cortex_controller_config(declared_protocol):
    return {
        "python": sys.executable,
        "contracts": {
            CONTRACT_NAME: {
                "version": 2,
                "producer": "Cortex",
                "producer_task_id": "CORTEX-001",
                "consumers": ["Evaluation"],
                "proposal_body": declared_protocol,
            }
        },
        "tasks": {"CORTEX-001": {"checks": []}},
    }


def integrate_cortex_producer(store, state, declared_protocol, cfg):
    integration = state / "integration"
    integration.mkdir()
    store.add_task(
        "CORTEX-001",
        "Cortex",
        "Publish protocol contract",
        scope=["src/cortex_protocol.py"],
    )
    attempt = store.claim("CORTEX-001", "synthetic-cortex-producer", 30)
    worker = state / "attempts" / str(attempt["id"]) / "checkout"
    task = store.get_task("CORTEX-001")
    plan = prepare_verification_plan(state, store, task, cfg)
    assert plan.execution_qualified
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )

    def git(repo, *args):
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            env=git_env,
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    git(integration, "init", "-b", "synthetic/base")
    git(integration, "config", "user.name", "synthetic")
    git(integration, "config", "user.email", "synthetic@example.invalid")
    (integration / "src").mkdir()
    (integration / "src" / "base.py").write_text("BASE = True\n")
    git(integration, "add", "-A")
    git(integration, "commit", "-m", "synthetic base")
    base = git(integration, "rev-parse", "HEAD")
    git(integration, "update-ref", GitGate.ACTIVE_REF, base)
    worker.parent.mkdir(parents=True)
    git(state, "clone", str(integration), str(worker))
    git(worker, "config", "user.name", "synthetic-worker")
    git(worker, "config", "user.email", "worker@example.invalid")
    source = worker / "src" / "cortex_protocol.py"
    protocol_json = json.dumps(declared_protocol, sort_keys=True, separators=(",", ":"))
    source.write_text("PROTOCOL_JSON = " + repr(protocol_json) + "\n")
    git(worker, "add", "-A")
    git(worker, "commit", "-m", "publish synthetic Cortex protocol contract")
    submitted = git(worker, "rev-parse", "HEAD")
    gate = GitGate(integration, integration, plan.policy, state / "receipts")
    candidate = gate.submit(worker, submitted, base, ["src/cortex_protocol.py"])
    receipt = gate.verify(
        candidate,
        lambda checkout, sha, _policy: {
            "status": "passed"
            if (checkout / "src/cortex_protocol.py").read_text()
            == "PROTOCOL_JSON = " + repr(protocol_json) + "\n"
            and git(checkout, "rev-parse", "HEAD") == sha
            else "failed"
        },
    )
    gate.publish(candidate, receipt.digest, base)

    intent = {
        "task": "CORTEX-001",
        "submitted_sha": candidate.submitted_sha,
        "attempt_id": attempt["id"],
        "epoch": attempt["epoch"],
        "candidate_sha": candidate.sha,
        "expected_tip": base,
        "receipt": receipt.digest,
    }
    evidence = {
        "checkout": str(worker),
        "base": base,
        "verification_plan": plan.identity(),
    }
    store.submit(attempt["id"], attempt["epoch"], candidate.submitted_sha, evidence)
    store.verify("CORTEX-001", candidate.submitted_sha, True, intent)
    store.accept("CORTEX-001", candidate.submitted_sha)
    store.integrate("CORTEX-001", candidate.submitted_sha, candidate.sha, intent)
    return candidate.submitted_sha, candidate.sha, receipt.digest, base


class SyntheticResearchProtocolReview:
    def __init__(self, expected_protocol, expected_hash, source_commit, receipt_sha):
        self.expected_protocol = expected_protocol
        self.expected_hash = expected_hash
        self.source_commit = source_commit
        self.receipt_sha = receipt_sha
        self.reviewed_teams = []
        self.started_teams = []

    def start(self, request):
        body = request["body"]
        assert body["packet"]["proposed_contract"] == self.expected_protocol
        assert body["contract_hash"] == self.expected_hash
        assert body["packet"]["source_commit"] == self.source_commit
        assert body["packet"]["evidence_refs"] == ["git-receipt:" + self.receipt_sha]
        assert body["team"] in {"Cortex", "Evaluation"}
        self.started_teams.append(body["team"])
        return {
            "thread_id": "synthetic-protocol-review-" + body["team"],
            "model": "synthetic-reviewer",
            "pid": 123,
            "birth": "synthetic-process",
        }

    def review(self, _session, request):
        body = request["body"]
        self.reviewed_teams.append(body["team"])
        result = {
            "decision": "approve",
            "compatibility": "compatible",
            "rationale": "Synthetic review of the exact protocol contract passed.",
            "findings": [],
            "reviewed_evidence_refs": body["packet"]["evidence_refs"],
            "affected_consumers_reviewed": body["packet"]["affected_consumer_task_ids"],
            "migration_plan": None,
        }
        return {
            "provider_turn_id": "synthetic-protocol-turn-" + body["team"],
            "raw_result": json.dumps(result, sort_keys=True, separators=(",", ":")).encode(),
        }


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


def test_controller_cortex_wrapper_separates_body_from_participant_metadata(tmp_path):
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
        value = protocol()
        cfg = cortex_controller_config(value)
        transitions = supervisor._configured_contract_transitions(cfg, store)
        assert len(transitions) == 1
        transition = transitions[0]["body"]
        assert transition["body"] == value
        assert transition["contract_hash"] == protocol_sha256(value, version=2)
        assert transition["producer_team"] == "Cortex"
        assert transition["consumer_teams"] == ["Evaluation"]

        bad = copy.deepcopy(cfg)
        bad["contracts"][CONTRACT_NAME]["unexpected"] = "must fail closed"
        with pytest.raises(StateError, match="contract configuration invalid"):
            supervisor._configured_contract_transitions(bad, store)
        assert store.get_contract_transition(CONTRACT_NAME, 2)["body"] == transition
    finally:
        store.close()


def test_reviewed_protocol_is_admitted_by_exact_hash_after_reopen(tmp_path):
    state = tmp_path / "controller"
    state.mkdir()
    path = state / "org.sqlite"
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
    cfg = cortex_controller_config(value)
    with patch.object(supervisor, "config", return_value=cfg):
        supervisor._configured_contract_transitions(cfg, store)
    submitted_sha, published_sha, receipt_sha, _base = integrate_cortex_producer(
        store, state, value, cfg
    )
    review_transport = SyntheticResearchProtocolReview(
        value, expected_hash, published_sha, receipt_sha
    )
    with patch.object(supervisor, "config", return_value=cfg):
        request_ids = supervisor.activate_contract(state, store)
        assert len(request_ids) == 2

        def dispatch_guard(request):
            supervisor._contract_review_dispatch_guard(state, store, cfg, request)

        def process_one():
            return process_pending_reviews(
                store,
                review_transport,
                limit=1,
                allowed_request_ids=request_ids,
                dispatch_guard=dispatch_guard,
            )

        receipt_path = state / "receipts" / f"{receipt_sha}.json"
        receipt_bytes = receipt_path.read_bytes()
        cfg["tasks"]["CORTEX-001"]["checks"] = [["/bin/false"]]
        stale_plan = process_one()
        assert stale_plan["transport_invocations"] == 0
        assert stale_plan["failures"]
        cfg["tasks"]["CORTEX-001"]["checks"] = []

        receipt_mode = stat.S_IMODE(receipt_path.stat().st_mode)
        receipt_path.chmod(receipt_mode | stat.S_IWUSR)
        receipt_path.write_bytes(b"tampered receipt")
        stale_receipt = process_one()
        assert stale_receipt["transport_invocations"] == 0
        assert stale_receipt["failures"]
        receipt_path.write_bytes(receipt_bytes)
        receipt_path.chmod(receipt_mode)

        (state / "STOP").touch()
        stopped = process_one()
        assert stopped["transport_invocations"] == 0
        assert stopped["failures"]
        assert review_transport.started_teams == []
        (state / "STOP").unlink()

        with patch.object(
            store,
            "replay_contract_review_response",
            side_effect=RuntimeError("synthetic interruption after response custody"),
        ):
            interrupted_review = process_one()
        assert interrupted_review["failures"]
        assert len(review_transport.started_teams) == 1
        first_team = review_transport.started_teams[0]
        first_request_id = next(
            request_id
            for request_id in request_ids
            if store.get_contract_review_request(request_id)["body"]["team"] == first_team
        )
        assert store.get_contract_review_response(first_request_id)["raw_result"]
        assert store.get_decision(store._contract_ack_key(CONTRACT_NAME, 2, first_team)) is None
        store.close()
        store = Store(path)
        replayed_ids = supervisor.activate_contract(state, store)
        assert replayed_ids == request_ids
        assert store.get_decision(store._contract_ack_key(CONTRACT_NAME, 2, first_team)) is not None
        assert len(review_transport.started_teams) == 1

        second_review = process_pending_reviews(
            store,
            review_transport,
            limit=1,
            allowed_request_ids=request_ids,
            dispatch_guard=lambda request: supervisor._contract_review_dispatch_guard(
                state, store, cfg, request
            ),
        )
        assert len(second_review["processed"]) == 1
        store.close()
        store = Store(path)
        supervisor.activate_contract(state, store)
        assert sorted(review_transport.reviewed_teams) == ["Cortex", "Evaluation"]
        assert store.get_contract(CONTRACT_NAME, 2)["body"] == value
        integration = store.get_integration("CORTEX-001")
        assert integration["candidate_sha"] == submitted_sha
        assert integration["integration_sha"] == published_sha
        assert integration["details"]["receipt"] == receipt_sha

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
