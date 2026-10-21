"""Durable, local engineering coordination. No executor or network access.

Each public mutation is one SQLite BEGIN IMMEDIATE transaction with an event.
WAL + synchronous FULL provide local crash durability (not a distributed store).
Execution is at-least-once: every claim gets a monotonically increasing fence;
only the current, unexpired attempt can submit. A submitted candidate is immutable
and verification/acceptance/integration always resolve its original attempt fence.
Dependencies require integration, so consumers start from integrated producer work.
Use one Store per process (multiple instances safely contend through SQLite).
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .task_execution import build_execution_binding, resolve_execution_binding
from .task_specs import validate_task_spec


class StateError(ValueError):
    """Invalid transition, conflicting immutable record, or malformed input."""


class StaleAttempt(StateError):
    """An expired, superseded, or otherwise inactive execution fence."""


_ACTIVE_TASK_STATES = ("pending", "running", "submitted", "verified", "accepted")
_TASK_STATES = (*_ACTIVE_TASK_STATES, "integrated", "failed")
_LIFECYCLE_OBSERVATION_FIELDS = frozenset(
    {
        "desired_state",
        "stop_reasons",
        "runtime_ready",
        "accounting_ready",
        "quota_ready",
        "process_state",
        "process_owner",
        "lease_owner",
        "active_attempt_id",
        "recovery_open",
        "backoff_until",
        "now",
        "launch_authorized",
    }
)
_LIFECYCLE_REQUEST_MAX_BYTES = 64 * 1024
_LIFECYCLE_WORKLIST_MAX_BYTES = 1024 * 1024
_CONTRACT_REVIEW_REPLAY_SCAN_LIMIT = 1000
_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES = 512 * 1024
_CONTRACT_REVIEW_REPLAY_REQUEST_MAX_BYTES = 2 * 1024 * 1024
_CONTRACT_REVIEW_REPLAY_INVENTORY_MAX_BYTES = 32 * 1024 * 1024
_CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY = (
    "SELECT name FROM decisions WHERE name>=? AND name<'contract-review0' ORDER BY name LIMIT 1"
)
_CONTRACT_REVIEW_GLOBAL_REPLAY_NEXT_QUERY = (
    "SELECT name FROM decisions WHERE name>? AND name<'contract-review0' ORDER BY name LIMIT 1"
)
_LIFECYCLE_REQUEST_FIRST_QUERY = (
    "SELECT name FROM decisions WHERE name>=? AND name<'lifecycle/request0' ORDER BY name LIMIT 1"
)
_LIFECYCLE_REQUEST_NEXT_QUERY = (
    "SELECT name FROM decisions WHERE name>? AND name<'lifecycle/request0' ORDER BY name LIMIT 1"
)
_CONTRACT_REVIEW_TRANSITION_REPLAY_FIRST_QUERY = (
    "SELECT name FROM decisions WHERE name GLOB 'contract-review/*' "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.name') END=? "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.version') END=? "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.contract_hash') END=? "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.proposal_digest') END=? "
    "ORDER BY name LIMIT 1"
)
_CONTRACT_REVIEW_TRANSITION_REPLAY_NEXT_QUERY = (
    "SELECT name FROM decisions WHERE name GLOB 'contract-review/*' "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.name') END=? "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.version') END=? "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.contract_hash') END=? "
    "AND CASE WHEN json_valid(body) THEN json_extract(body,'$.proposal_digest') END=? "
    "AND name>? ORDER BY name LIMIT 1"
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strict_contract_review_result(raw_result: bytes) -> dict:
    """Decode one durable review result, rejecting duplicate keys and non-object roots."""
    if type(raw_result) is not bytes:
        raise ValueError("contract review response bytes invalid")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    value = json.loads(raw_result.decode("utf-8"), object_pairs_hook=unique_object)
    if type(value) is not dict:
        raise ValueError("contract review response root is not an object")
    _json(value)  # Reject non-finite JSON values accepted by Python's decoder.
    return value


def _name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateError("identifier must be a nonempty string")
    return value


def _positive(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateError("positive finite number required")
    if not math.isfinite(value) or value <= 0:
        raise StateError("positive finite number required")
    return value


def _lifecycle_snapshot(value: Any) -> Any:
    """Detach caller-owned JSON data before a lifecycle transaction can wait on SQLite."""
    try:
        return json.loads(_json(value))
    except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
        raise StateError("invalid_lifecycle_input") from None


class Store:
    """Small transactional API; returned dictionaries are detached snapshots.

    Task state: pending -> running -> submitted -> verified -> accepted -> integrated.
    Failure/expiry returns to pending until max_attempts is exhausted, then failed.
    Versions are append-only; contract requirements match the current version exactly.
    A controller, separate from worker execution, owns verify/accept/integrate calls.
    """

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(
            str(path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS objectives(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, brief TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, team TEXT NOT NULL, title TEXT NOT NULL,
            brief TEXT NOT NULL, scope TEXT NOT NULL, acceptance TEXT NOT NULL,
            dependencies TEXT NOT NULL, contracts TEXT NOT NULL, base_sha TEXT NOT NULL,
            objective_id TEXT REFERENCES objectives(id), max_attempts INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', epoch INTEGER NOT NULL DEFAULT 0,
            active_attempt INTEGER, candidate_sha TEXT);
          CREATE TABLE IF NOT EXISTS attempts(
            id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            epoch INTEGER NOT NULL, worker TEXT NOT NULL, lease_until REAL NOT NULL,
            state TEXT NOT NULL, session TEXT, UNIQUE(task_id,epoch));
          CREATE TABLE IF NOT EXISTS contracts(
            name TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(name,version));
          CREATE TABLE IF NOT EXISTS decisions(
            name TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
            replaces TEXT, replaces_version INTEGER, PRIMARY KEY(name,version));
          CREATE INDEX IF NOT EXISTS idx_contract_review_transition_replay
            ON decisions(
              CASE WHEN json_valid(body) THEN json_extract(body,'$.name') END,
              CASE WHEN json_valid(body) THEN json_extract(body,'$.version') END,
              CASE WHEN json_valid(body) THEN json_extract(body,'$.contract_hash') END,
              CASE WHEN json_valid(body) THEN json_extract(body,'$.proposal_digest') END,
              name,version
            ) WHERE name GLOB 'contract-review/*';
          CREATE TABLE IF NOT EXISTS candidates(
            task_id TEXT NOT NULL REFERENCES tasks(id), sha TEXT NOT NULL,
            attempt_id INTEGER NOT NULL REFERENCES attempts(id), epoch INTEGER NOT NULL,
            evidence TEXT NOT NULL, PRIMARY KEY(task_id,sha));
          CREATE TABLE IF NOT EXISTS verifications(
            id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, sha TEXT NOT NULL,
            attempt_id INTEGER NOT NULL, epoch INTEGER NOT NULL,
            passed INTEGER NOT NULL, details TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS acceptances(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), sha TEXT NOT NULL,
            attempt_id INTEGER NOT NULL, epoch INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS integrations(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), sha TEXT NOT NULL,
            integration_sha TEXT NOT NULL, details TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS usage(
            id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id),
            epoch INTEGER NOT NULL, body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS task_specs(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), body TEXT NOT NULL,
            policy_sha256 TEXT NOT NULL, admission TEXT NOT NULL,
            decision_name TEXT NOT NULL, decision_version INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL,
            entity TEXT NOT NULL, body TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS idx_tasks_state_id ON tasks(state,id);
        """)
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS quiescence_revision(
            id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
          INSERT OR IGNORE INTO quiescence_revision(id,value) VALUES(1,0);
          CREATE TRIGGER IF NOT EXISTS quiescence_tasks_insert
            AFTER INSERT ON tasks BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_tasks_update
            AFTER UPDATE ON tasks BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_tasks_delete
            AFTER DELETE ON tasks BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_contracts_insert
            AFTER INSERT ON contracts BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_contracts_update
            AFTER UPDATE ON contracts BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_contracts_delete
            AFTER DELETE ON contracts BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_contract_decisions_insert
            AFTER INSERT ON decisions
            WHEN NEW.name GLOB 'contract-transition/*'
              OR NEW.name GLOB 'contract-ack/*'
              OR NEW.name GLOB 'contract-review/*'
            BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_contract_decisions_update
            AFTER UPDATE ON decisions
            WHEN OLD.name GLOB 'contract-transition/*'
              OR OLD.name GLOB 'contract-ack/*'
              OR OLD.name GLOB 'contract-review/*'
              OR NEW.name GLOB 'contract-transition/*'
              OR NEW.name GLOB 'contract-ack/*'
              OR NEW.name GLOB 'contract-review/*'
            BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
          CREATE TRIGGER IF NOT EXISTS quiescence_contract_decisions_delete
            AFTER DELETE ON decisions
            WHEN OLD.name GLOB 'contract-transition/*'
              OR OLD.name GLOB 'contract-ack/*'
              OR OLD.name GLOB 'contract-review/*'
            BEGIN
              UPDATE quiescence_revision SET value=value+1 WHERE id=1;
            END;
        """)
        self._issued_quiescence_snapshot_sha256: bytes | None = None

    def admit_task_spec(
        self,
        spec: Any,
        *,
        envelope: Any,
        acceptance_registry: Any,
        policy_sha256: str,
        admission: Any,
        verifier_registry: Any = None,
        controller_release_sha256: str | None = None,
        integration_policy_sha256: str | None = None,
    ) -> str:
        if type(policy_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", policy_sha256) is None:
            raise StateError("invalid_task_spec_policy_hash")
        if type(admission) is not dict:
            raise StateError("invalid_task_spec_admission")
        try:
            admission_snapshot = json.loads(_json(admission))
        except (TypeError, ValueError, OverflowError, RuntimeError):
            raise StateError("invalid_task_spec_admission") from None
        if type(admission_snapshot) is not dict or set(admission_snapshot) != {
            "rationale",
            "evidence",
        }:
            raise StateError("invalid_task_spec_admission")
        for key in ("rationale", "evidence"):
            value = admission_snapshot[key]
            if type(value) is not str or not value.strip() or not 10 <= len(value) <= 4000:
                raise StateError("invalid_task_spec_admission")
        proposed_id = spec.get("id") if type(spec) is dict else None
        if type(proposed_id) is not str:
            # Let the canonical validator provide its stable rejection code.
            proposed_id = None
        decision_name = "admission/task-spec/" + proposed_id if proposed_id else None
        with self._tx():
            if (
                proposed_id
                and self.db.execute("SELECT 1 FROM tasks WHERE id=?", (proposed_id,)).fetchone()
            ):
                raise StateError("task_spec_duplicate")
            if (
                decision_name
                and self.db.execute(
                    "SELECT 1 FROM decisions WHERE name=?", (decision_name,)
                ).fetchone()
            ):
                raise StateError("task_spec_duplicate")
            if (
                type(spec) is dict
                and type(spec.get("objective_id")) is str
                and not self.db.execute(
                    "SELECT 1 FROM objectives WHERE id=?", (spec["objective_id"],)
                ).fetchone()
            ):
                raise StateError("task_spec_objective_missing")
            existing = self.list_tasks()
            try:
                canonical = validate_task_spec(
                    spec,
                    envelope=envelope,
                    acceptance_registry=acceptance_registry,
                    existing_tasks=existing,
                )
            except StateError as exc:
                if str(exc) == "duplicate_task_id":
                    raise StateError("task_spec_duplicate") from None
                raise
            task_id = canonical["id"]
            execution_binding = None
            binding_requested = any(
                value is not None
                for value in (
                    verifier_registry,
                    controller_release_sha256,
                    integration_policy_sha256,
                )
            )
            if binding_requested:
                if (
                    verifier_registry is None
                    or controller_release_sha256 is None
                    or integration_policy_sha256 is None
                ):
                    raise StateError("incomplete_task_execution_binding")
                try:
                    execution_binding = build_execution_binding(
                        canonical,
                        verifier_registry=verifier_registry,
                        controller_release_sha256=controller_release_sha256,
                        integration_policy_sha256=integration_policy_sha256,
                    )
                except ValueError as exc:
                    raise StateError(str(exc)) from None
            decision_name = "admission/task-spec/" + task_id
            collision = (
                self.db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
                or self.db.execute(
                    "SELECT 1 FROM decisions WHERE name=?", (decision_name,)
                ).fetchone()
            )
            if collision:
                raise StateError("task_spec_duplicate")
            if not self.db.execute(
                "SELECT 1 FROM objectives WHERE id=?", (canonical["objective_id"],)
            ).fetchone():
                raise StateError("task_spec_objective_missing")
            task_contracts = {}
            for name, requirement in canonical["contracts"].items():
                row = self.db.execute(
                    "SELECT MAX(version) FROM contracts WHERE name=?", (name,)
                ).fetchone()
                if row[0] != requirement["version"]:
                    raise StateError("task_spec_contract_not_current")
                task_contracts[name] = requirement["version"]
            body = _json(canonical)
            admission_body = _json(admission_snapshot)
            fields = (
                canonical["id"],
                canonical["team"],
                canonical["title"],
                canonical["brief"],
                _json(canonical["scope"]),
                _json([f"{a['id']}:{a['sha256']}" for a in canonical["acceptance"]]),
                _json(canonical["dependencies"]),
                _json(task_contracts),
                canonical["base_sha"],
                canonical["objective_id"],
                canonical["resources"]["max_attempts"],
            )
            self.db.execute(
                """INSERT INTO tasks(
                  id,team,title,brief,scope,acceptance,dependencies,contracts,
                  base_sha,objective_id,max_attempts
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                fields,
            )
            self._event("task_added", task_id, self._task(task_id))
            decision_body = {
                "spec": canonical,
                "policy_sha256": policy_sha256,
                "admission": admission_snapshot,
                "execution_binding": execution_binding,
            }
            self.db.execute(
                """INSERT INTO decisions(
                  name,version,body,replaces,replaces_version
                ) VALUES(?,?,?,?,?)""",
                (decision_name, 1, _json(decision_body), None, None),
            )
            self._event(
                "decision_recorded",
                decision_name,
                {
                    "version": 1,
                    "body": decision_body,
                    "replaces": None,
                    "replaces_version": None,
                },
            )
            self.db.execute(
                """INSERT INTO task_specs(
                  task_id,body,policy_sha256,admission,decision_name,decision_version
                ) VALUES(?,?,?,?,?,1)""",
                (task_id, body, policy_sha256, admission_body, decision_name),
            )
            self._event(
                "task_spec_admitted",
                task_id,
                {"decision": decision_name, "version": 1, "policy_sha256": policy_sha256},
            )
            return task_id

    def get_task_spec(self, task_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM task_specs WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return None
            return {
                "spec": json.loads(row["body"]),
                "policy_sha256": row["policy_sha256"],
                "admission": json.loads(row["admission"]),
                "decision": {"name": row["decision_name"], "version": row["decision_version"]},
            }

    def resolve_task_execution_binding(
        self, task_id: str, *, controller_release_sha256: str
    ) -> dict:
        """Read the exact immutable admission decision referenced by the spec row."""
        with self._read_snapshot():
            row = self.db.execute(
                "SELECT body,policy_sha256,admission,decision_name,decision_version "
                "FROM task_specs WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise StateError("missing_execution_binding")
            decision_row = self.db.execute(
                "SELECT name,version,body FROM decisions WHERE name=? AND version=?",
                (row["decision_name"], row["decision_version"]),
            ).fetchone()
            if decision_row is None:
                raise StateError("missing_execution_binding_decision")
            decision = {**dict(decision_row), "body": json.loads(decision_row["body"])}
            decision_body = decision["body"]
            if (
                _json(decision_body.get("spec")) != _json(json.loads(row["body"]))
                or decision_body.get("policy_sha256") != row["policy_sha256"]
                or _json(decision_body.get("admission")) != _json(json.loads(row["admission"]))
            ):
                raise StateError("execution_admission_record_mismatch")
            try:
                return resolve_execution_binding(
                    task_id,
                    self._task(task_id),
                    decision,
                    controller_release_sha256=controller_release_sha256,
                )
            except ValueError as exc:
                raise StateError(str(exc)) from None

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    @contextmanager
    def _read_snapshot(self):
        with self._lock:
            self.db.execute("BEGIN")
            try:
                yield
            finally:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")

    def _event(self, kind: str, entity: str, body: Any) -> None:
        self.db.execute(
            "INSERT INTO events(at,kind,entity,body) VALUES(?,?,?,?)",
            (self.clock(), kind, entity, _json(body)),
        )

    def _task(self, task_id: str) -> dict:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise StateError(f"unknown task: {task_id}")
        result = dict(row)
        for key in ("scope", "acceptance", "dependencies", "contracts"):
            result[key] = json.loads(result[key])
        return result

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            return self._task(task_id)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [self._task(r[0]) for r in self.db.execute("SELECT id FROM tasks ORDER BY id")]

    def active_task_projection(self, limit: int = 100) -> dict:
        """Return a bounded active-task view while retaining full state counts and history."""
        if type(limit) is not int or not 0 <= limit <= 1000:
            raise StateError("active task projection limit must be between zero and one thousand")
        with self._lock:
            if self.db.in_transaction:
                return self._active_task_projection(limit)
            with self._read_snapshot():
                return self._active_task_projection(limit)

    def _active_task_projection(self, limit: int) -> dict:
        count_row = self.db.execute(
            """SELECT COUNT(*) AS total_count,
              SUM(state='pending') AS pending,
              SUM(state='running') AS running,
              SUM(state='submitted') AS submitted,
              SUM(state='verified') AS verified,
              SUM(state='accepted') AS accepted,
              SUM(state='integrated') AS integrated,
              SUM(state='failed') AS failed,
              SUM(state NOT IN ('pending','running','submitted','verified','accepted',
                                'integrated','failed')) AS unclassified
              FROM tasks"""
        ).fetchone()
        state_counts = {state: count_row[state] for state in _TASK_STATES if count_row[state]}
        unclassified_count = count_row["unclassified"] or 0
        active_count = sum(state_counts.get(state, 0) for state in _ACTIVE_TASK_STATES)
        rows = self.db.execute(
            "SELECT id FROM tasks WHERE state IN (?,?,?,?,?) ORDER BY state,id LIMIT ?",
            (*_ACTIVE_TASK_STATES, limit),
        )
        if unclassified_count:
            state_counts["unclassified"] = unclassified_count
        return {
            "tasks": [self._task(row[0]) for row in rows],
            "total_count": count_row["total_count"],
            "active_count": active_count,
            "omitted_active_count": active_count - min(active_count, limit),
            "unclassified_count": unclassified_count,
            "state_counts": state_counts,
        }

    def add_objective(self, objective_id: str, title: str, brief: str = "") -> None:
        with self._tx():
            self.db.execute(
                "INSERT INTO objectives VALUES(?,?,?)", (_name(objective_id), _name(title), brief)
            )
            self._event("objective_added", objective_id, {"title": title, "brief": brief})

    def add_task(
        self,
        task_id: str,
        team: str,
        title: str,
        brief: str = "",
        *,
        scope=(),
        acceptance=(),
        dependencies=(),
        contracts=None,
        base_sha="",
        objective_id=None,
        max_attempts=3,
    ) -> None:
        _name(task_id), _name(team), _name(title)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise StateError("max_attempts must be a positive integer")
        scope, acceptance, dependencies = list(scope), list(acceptance), list(dependencies)
        for value in scope + acceptance + dependencies:
            _name(value)
        if len(set(dependencies)) != len(dependencies):
            raise StateError("duplicate dependencies")
        contracts = dict(contracts or {})
        for name, version in contracts.items():
            _name(name)
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise StateError("contract versions must be positive integers")
        with self._tx():
            # Existing dependencies only: immutable insertion order guarantees an acyclic DAG.
            for dependency in dependencies:
                self._task(dependency)
            self.db.execute(
                """INSERT INTO tasks(id,team,title,brief,scope,acceptance,
              dependencies,contracts,base_sha,objective_id,max_attempts)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    team,
                    title,
                    brief,
                    _json(scope),
                    _json(acceptance),
                    _json(dependencies),
                    _json(contracts),
                    base_sha,
                    objective_id,
                    max_attempts,
                ),
            )
            self._event("task_added", task_id, self._task(task_id))

    def _ready(self, task: dict) -> bool:
        return all(self._task(d)["state"] == "integrated" for d in task["dependencies"]) and all(
            self.db.execute("SELECT MAX(version) FROM contracts WHERE name=?", (name,)).fetchone()[
                0
            ]
            == version
            for name, version in task["contracts"].items()
        )

    def readiness_report(self) -> list[dict]:
        """Return one coherent, read-only snapshot of pending work and explicit blockers.

        A blocked task does not hide unrelated ready tasks. This report is advisory; `claim`
        remains the transactional authority that rechecks readiness.
        """
        with self._read_snapshot():
            return self._readiness_report_in_snapshot()

    def quiescence_snapshot(self) -> dict:
        """Return task readiness and pending contract reviews from one SQLite snapshot."""
        with self._read_snapshot():
            revision = self.db.execute(
                "SELECT value FROM quiescence_revision WHERE id=1"
            ).fetchone()[0]
            tasks = self.list_tasks()
            snapshot = {
                "revision": revision,
                "tasks": [
                    {
                        "task_id": task["id"],
                        "state": task["state"],
                        "candidate_sha": task["candidate_sha"],
                    }
                    for task in tasks
                ],
                "readiness": self._readiness_report_in_snapshot(),
                "contract_reviews": self._contract_transition_review_projection_in_snapshot(
                    limit=100
                ),
                "review_requests": self._contract_review_worklist_in_snapshot(
                    limit=100, overflow_as_result=True
                ),
            }
            self._issued_quiescence_snapshot_sha256 = hashlib.sha256(
                _json(snapshot).encode("utf-8")
            ).digest()
            return snapshot

    def record_quiescence_handoff_if_current(
        self, fingerprint: Any, expected_snapshot: Any, body: Any
    ) -> bool:
        """Atomically record an inert quiescence handoff only if its snapshot is current."""
        if type(fingerprint) is not str or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise StateError("quiescence handoff fingerprint invalid")
        expected_snapshot = _lifecycle_snapshot(expected_snapshot)
        body = _lifecycle_snapshot(body)
        if (
            type(expected_snapshot) is not dict
            or set(expected_snapshot)
            != {"revision", "tasks", "readiness", "contract_reviews", "review_requests"}
            or type(expected_snapshot.get("revision")) is not int
            or expected_snapshot["revision"] < 0
            or type(body) is not dict
            or set(body)
            != {
                "owner",
                "status",
                "reason",
                "resource_reason",
                "fingerprint",
                "review_requests",
            }
            or body.get("owner") != "Sol"
            or body.get("status") != "requested"
            or body.get("fingerprint") != fingerprint
            or type(body.get("reason")) is not str
            or type(body.get("resource_reason")) is not str
            or body.get("review_requests") != expected_snapshot.get("review_requests")
        ):
            raise StateError("quiescence handoff body invalid")
        if body["reason"] not in {
            "quota-unavailable",
            "blocked-dependencies",
            "catalogue-exhausted",
            "contract-reviews-pending",
        }:
            raise StateError("quiescence handoff reason invalid")
        identity = {
            "snapshot": expected_snapshot,
            "resource_reason": body["resource_reason"],
            "reason": body["reason"],
        }
        expected_fingerprint = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
        if fingerprint != expected_fingerprint:
            raise StateError("quiescence handoff fingerprint mismatch")
        expected_snapshot_sha256 = hashlib.sha256(_json(expected_snapshot).encode("utf-8")).digest()
        name = "sol-handoff/" + fingerprint
        with self._tx():
            current_revision = self.db.execute(
                "SELECT value FROM quiescence_revision WHERE id=1"
            ).fetchone()[0]
            if (
                self._issued_quiescence_snapshot_sha256 != expected_snapshot_sha256
                or current_revision != expected_snapshot["revision"]
            ):
                return False
            existing = self.get_decision(name)
            if existing is not None:
                if _json(existing["body"]) != _json(body):
                    raise StateError("quiescence handoff identity conflict")
                return True
            self._put_decision_in_transaction(name, body)
            return True

    def _readiness_report_in_snapshot(self) -> list[dict]:
        """Build readiness from the current transaction or read snapshot."""
        tasks = self.list_tasks()
        by_id = {task["id"]: task for task in tasks}
        report = []
        for task in tasks:
            blockers = []
            for dependency_id in task["dependencies"]:
                dependency = by_id.get(dependency_id)
                state = None if dependency is None else dependency["state"]
                if state != "integrated":
                    blockers.append(
                        {
                            "kind": "dependency",
                            "id": dependency_id,
                            "state": state,
                        }
                    )
            for contract_name, required_version in task["contracts"].items():
                row = self.db.execute(
                    "SELECT MAX(version) FROM contracts WHERE name=?", (contract_name,)
                ).fetchone()
                current_version = row[0]
                if current_version != required_version:
                    blockers.append(
                        {
                            "kind": "contract",
                            "name": contract_name,
                            "required_version": required_version,
                            "current_version": current_version,
                        }
                    )
            report.append(
                {
                    "task_id": task["id"],
                    "state": task["state"],
                    "eligible": task["state"] == "pending" and not blockers,
                    "blockers": blockers,
                }
            )
        return report

    def ready_tasks(self, team: str | None = None) -> list[dict]:
        with self._tx():
            self._recover()
            return [
                t
                for t in self.list_tasks()
                if t["state"] == "pending"
                and (team is None or t["team"] == team)
                and self._ready(t)
            ]

    def _retry_state(self, task: dict, retryable: bool) -> str:
        return "pending" if retryable and task["epoch"] < task["max_attempts"] else "failed"

    def _recover(self) -> list[int]:
        expired = list(
            self.db.execute(
                "SELECT * FROM attempts WHERE state='running' AND lease_until<=?", (self.clock(),)
            )
        )
        for attempt in expired:
            task = self._task(attempt["task_id"])
            self.db.execute("UPDATE attempts SET state='expired' WHERE id=?", (attempt["id"],))
            state = self._retry_state(task, True)
            self.db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task["id"]))
            self._event(
                "attempt_expired", task["id"], {"attempt_id": attempt["id"], "state": state}
            )
        return [r["id"] for r in expired]

    def recover_expired(self) -> list[int]:
        with self._tx():
            return self._recover()

    def claim(self, task_id: str, worker: str, lease_seconds: float = 300) -> dict | None:
        _name(worker)
        _positive(lease_seconds)
        with self._tx():
            self._recover()
            task = self._task(task_id)
            if task["state"] != "pending" or not self._ready(task):
                return None
            epoch = task["epoch"] + 1
            cursor = self.db.execute(
                """INSERT INTO attempts(task_id,epoch,worker,lease_until,state)
               VALUES(?,?,?,?,'running')""",
                (task_id, epoch, worker, self.clock() + lease_seconds),
            )
            attempt_id = cursor.lastrowid
            self.db.execute(
                """UPDATE tasks SET state='running',epoch=?,active_attempt=?,candidate_sha=NULL
                               WHERE id=?""",
                (epoch, attempt_id, task_id),
            )
            attempt = dict(
                self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            )
            self._event("attempt_claimed", task_id, attempt)
            return attempt

    def _fence(self, attempt_id: int, epoch: int, *, running: bool = True) -> tuple[dict, dict]:
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise StaleAttempt("unknown attempt")
        attempt = dict(row)
        task = self._task(attempt["task_id"])
        if (
            attempt["epoch"] != epoch
            or task["epoch"] != epoch
            or task["active_attempt"] != attempt_id
        ):
            raise StaleAttempt("superseded attempt fence")
        if running and (attempt["state"] != "running" or attempt["lease_until"] <= self.clock()):
            raise StaleAttempt("attempt is not running under a valid lease")
        return task, attempt

    def renew(self, attempt_id: int, epoch: int, lease_seconds: float = 300) -> float:
        _positive(lease_seconds)
        with self._tx():
            task, _ = self._fence(attempt_id, epoch)
            until = self.clock() + lease_seconds
            self.db.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, attempt_id))
            self._event(
                "lease_renewed",
                task["id"],
                {"attempt_id": attempt_id, "epoch": epoch, "lease_until": until},
            )
            return until

    def record_session(
        self, attempt_id: int, epoch: int, thread_id: str, model: str, runner: Any = None
    ) -> None:
        _name(thread_id), _name(model)
        session = {"thread_id": thread_id, "model": model, "runner": runner}
        with self._tx():
            task, attempt = self._fence(attempt_id, epoch)
            if attempt["session"] is not None and json.loads(attempt["session"]) != session:
                raise StateError("session identity is immutable within an attempt")
            self.db.execute(
                "UPDATE attempts SET session=? WHERE id=?", (_json(session), attempt_id)
            )
            self._event("session_recorded", task["id"], {"attempt_id": attempt_id, **session})

    def record_stage_session(
        self,
        attempt_id: int,
        epoch: int,
        thread_id: str,
        model: str,
        runner: Any = None,
        *,
        stage: str,
    ) -> None:
        """Append one immutable named stage; latest session is the recovery pointer.

        Identical retries are idempotent. A stage cannot be rebound to a different
        thread/model/runner. History remains available in session_stage_recorded events.
        """
        _name(stage), _name(thread_id), _name(model)
        session = {"thread_id": thread_id, "model": model, "runner": runner, "stage": stage}
        with self._tx():
            task, _ = self._fence(attempt_id, epoch)
            for row in self.db.execute(
                "SELECT body FROM events WHERE kind='session_stage_recorded' AND entity=?",
                (task["id"],),
            ):
                old = json.loads(row["body"])
                if old["attempt_id"] == attempt_id and old["session"]["stage"] == stage:
                    if old["session"] != session:
                        raise StateError("stage session identity is immutable")
                    return
            self.db.execute(
                "UPDATE attempts SET session=? WHERE id=?", (_json(session), attempt_id)
            )
            self._event(
                "session_stage_recorded",
                task["id"],
                {"attempt_id": attempt_id, "epoch": epoch, "session": session},
            )

    def fail(self, attempt_id: int, epoch: int, reason: str, retryable: bool = True) -> None:
        with self._tx():
            task, _ = self._fence(attempt_id, epoch)
            state = self._retry_state(task, retryable)
            self.db.execute("UPDATE attempts SET state='failed' WHERE id=?", (attempt_id,))
            self.db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task["id"]))
            self._event(
                "attempt_failed",
                task["id"],
                {"attempt_id": attempt_id, "epoch": epoch, "reason": reason, "state": state},
            )

    def reopen_failed(
        self,
        task_id: str,
        recovery_id: str,
        *,
        reason: str,
        evidence_refs,
        extra_attempts: int = 3,
    ) -> dict:
        """Trusted reviewed recovery; preserve every failed attempt and usage record.

        This API supplies no worker capability or resource budget. The caller must
        first qualify the controller fix and authorize recovery. One immutable
        recovery ID grants at most three fresh attempts; exact replay is a no-op.
        """
        _name(task_id), _name(recovery_id), _name(reason)
        if len(recovery_id) > 200 or len(reason) > 4000:
            raise StateError("recovery reason and ID must be bounded")
        if type(extra_attempts) is not int or not 1 <= extra_attempts <= 3:
            raise StateError("recovery permits one to three extra attempts")
        if not isinstance(evidence_refs, (list, tuple)) or not 1 <= len(evidence_refs) <= 20:
            raise StateError("recovery requires bounded evidence references")
        for reference in evidence_refs:
            _name(reference)
            if len(reference) > 500:
                raise StateError("recovery evidence reference is too long")
        request = {
            "task_id": task_id,
            "recovery_id": recovery_id,
            "reason": reason,
            "evidence_refs": list(evidence_refs),
            "extra_attempts": extra_attempts,
        }
        key = "recovery/" + recovery_id + "/" + task_id
        with self._tx():
            task = self._task(task_id)
            prior = self.get_decision(key)
            if prior is not None:
                if prior["body"]["request"] != request:
                    raise StateError("recovery ID cannot be reused with different content")
                return task
            if task["state"] != "failed":
                raise StateError("only failed tasks can be reopened")
            if (
                self.db.execute(
                    "SELECT 1 FROM attempts WHERE task_id=? AND state IN ('running','submitted')",
                    (task_id,),
                ).fetchone()
                or self.db.execute(
                    "SELECT 1 FROM acceptances WHERE task_id=?", (task_id,)
                ).fetchone()
                or self.db.execute(
                    "SELECT 1 FROM integrations WHERE task_id=?", (task_id,)
                ).fetchone()
            ):
                raise StateError("active or accepted task cannot be reopened")
            new_max = task["epoch"] + extra_attempts
            body = {
                "request": request,
                "previous_max_attempts": task["max_attempts"],
                "new_max_attempts": new_max,
                "preserved_epoch": task["epoch"],
                "previous_attempt": task["active_attempt"],
                "previous_candidate_sha": task["candidate_sha"],
            }
            self.db.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?)", (key, 1, _json(body), None, None)
            )
            self._event(
                "decision_recorded",
                key,
                {"version": 1, "body": body, "replaces": None, "replaces_version": None},
            )
            self.db.execute(
                "UPDATE tasks SET state='pending',max_attempts=?,active_attempt=NULL,"
                "candidate_sha=NULL WHERE id=?",
                (new_max, task_id),
            )
            self._event("task_reopened", task_id, {"decision": key, **body})
            return self._task(task_id)

    def submit(self, attempt_id: int, epoch: int, candidate_sha: str, evidence: Any) -> dict:
        _name(candidate_sha)
        with self._tx():
            task, _ = self._fence(attempt_id, epoch)
            if not self._ready(task):
                raise StateError("dependency or contract changed during execution")
            self.db.execute(
                "INSERT INTO candidates VALUES(?,?,?,?,?)",
                (task["id"], candidate_sha, attempt_id, epoch, _json(evidence)),
            )
            self.db.execute("UPDATE attempts SET state='submitted' WHERE id=?", (attempt_id,))
            self.db.execute(
                "UPDATE tasks SET state='submitted',candidate_sha=? WHERE id=?",
                (candidate_sha, task["id"]),
            )
            self._event(
                "candidate_submitted",
                task["id"],
                {
                    "sha": candidate_sha,
                    "attempt_id": attempt_id,
                    "epoch": epoch,
                    "evidence": evidence,
                },
            )
            return {
                "task_id": task["id"],
                "sha": candidate_sha,
                "attempt_id": attempt_id,
                "epoch": epoch,
                "evidence": evidence,
            }

    def _candidate(self, task_id: str, candidate_sha: str) -> tuple[dict, dict]:
        task = self._task(task_id)
        row = self.db.execute(
            "SELECT * FROM candidates WHERE task_id=? AND sha=?", (task_id, candidate_sha)
        ).fetchone()
        if row is None or task["candidate_sha"] != candidate_sha:
            raise StaleAttempt("candidate is not current")
        candidate = dict(row)
        self._fence(candidate["attempt_id"], candidate["epoch"], running=False)
        if not self._ready(task):
            raise StateError("dependency or contract no longer current")
        return task, candidate

    def get_candidate(self, task_id: str, candidate_sha: str | None = None) -> dict | None:
        """Read immutable evidence, including historical candidates, for controller resume."""
        with self._lock:
            if candidate_sha is None:
                candidate_sha = self._task(task_id)["candidate_sha"]
            row = self.db.execute(
                "SELECT * FROM candidates WHERE task_id=? AND sha=?", (task_id, candidate_sha)
            ).fetchone()
            return None if row is None else {**dict(row), "evidence": json.loads(row["evidence"])}

    def verify(self, task_id: str, candidate_sha: str, passed: bool, details: Any) -> None:
        if not isinstance(passed, bool):
            raise StateError("verification result must be boolean")
        with self._tx():
            task, candidate = self._candidate(task_id, candidate_sha)
            if task["state"] != "submitted":
                raise StateError("verification requires submitted task")
            self.db.execute(
                "INSERT INTO verifications(task_id,sha,attempt_id,epoch,passed,details) "
                "VALUES(?,?,?,?,?,?)",
                (
                    task_id,
                    candidate_sha,
                    candidate["attempt_id"],
                    candidate["epoch"],
                    passed,
                    _json(details),
                ),
            )
            state = "verified" if passed else self._retry_state(task, True)
            self.db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task_id))
            if not passed:
                self.db.execute(
                    "UPDATE attempts SET state='rejected' WHERE id=?", (candidate["attempt_id"],)
                )
            self._event(
                "candidate_verified",
                task_id,
                {"sha": candidate_sha, "passed": passed, "details": details, "state": state},
            )

    def accept(self, task_id: str, candidate_sha: str) -> None:
        with self._tx():
            task, candidate = self._candidate(task_id, candidate_sha)
            if task["state"] != "verified":
                raise StateError("acceptance requires successful independent verification")
            self.db.execute(
                "INSERT INTO acceptances VALUES(?,?,?,?)",
                (task_id, candidate_sha, candidate["attempt_id"], candidate["epoch"]),
            )
            self.db.execute("UPDATE tasks SET state='accepted' WHERE id=?", (task_id,))
            self._event(
                "candidate_accepted",
                task_id,
                {
                    "sha": candidate_sha,
                    "attempt_id": candidate["attempt_id"],
                    "epoch": candidate["epoch"],
                },
            )

    def integrate(
        self, task_id: str, candidate_sha: str, integration_sha: str, details: Any = None
    ) -> None:
        _name(integration_sha)
        with self._tx():
            task, _ = self._candidate(task_id, candidate_sha)
            if task["state"] != "accepted":
                raise StateError("integration requires accepted candidate")
            self.db.execute(
                "INSERT INTO integrations VALUES(?,?,?,?)",
                (task_id, candidate_sha, integration_sha, _json(details)),
            )
            self.db.execute("UPDATE tasks SET state='integrated' WHERE id=?", (task_id,))
            self._event(
                "candidate_integrated",
                task_id,
                {"sha": candidate_sha, "integration_sha": integration_sha, "details": details},
            )

    def put_contract(self, name: str, version: int, body: Any) -> None:
        _name(name)
        if type(version) is not int or version < 1:
            raise StateError("contract version must be positive integer")
        if version != 1:
            raise StateError("contract version advances require an acknowledged transition")
        try:
            body_json = _json(body)
            body = json.loads(body_json)
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract body must be finite JSON data") from None
        with self._tx():
            if self.db.execute("SELECT 1 FROM contracts WHERE name=? LIMIT 1", (name,)).fetchone():
                raise StateError("contract version advances require an acknowledged transition")
            self._put_contract_in_transaction(name, version, body, body_json)

    def _single_contract_decision(self, name: str) -> dict | None:
        versions = [
            row[0]
            for row in self.db.execute(
                "SELECT version FROM decisions WHERE name=? ORDER BY version", (name,)
            )
        ]
        if not versions:
            return None
        if versions != [1]:
            raise StateError("contract decision history invalid")
        row = self.get_decision(name, 1)
        if row is None:
            raise StateError("contract decision history invalid")
        events = self.db.execute(
            "SELECT body FROM events WHERE kind='decision_recorded' AND entity=? ORDER BY seq",
            (name,),
        ).fetchall()
        if len(events) != 1:
            raise StateError("contract decision history invalid")
        expected_event = {
            "version": 1,
            "body": row["body"],
            "replaces": None,
            "replaces_version": None,
        }
        if events[0]["body"] != _json(expected_event):
            raise StateError("contract decision history invalid")
        return row

    def contract_transition_review_projection(self, *, limit: int = 100) -> dict:
        """List exact missing participant reviews without depending on runnable tasks."""
        if type(limit) is not int or not 0 <= limit <= 100:
            raise StateError("contract review limit must be between 0 and 100")
        with self._read_snapshot():
            return self._contract_transition_review_projection_in_snapshot(limit=limit)

    def _contract_transition_review_projection_in_snapshot(self, *, limit: int = 100) -> dict:
        """Build the passive contract-review lane inside an existing SQLite snapshot."""
        if type(limit) is not int or not 0 <= limit <= 100:
            raise StateError("contract review limit must be between 0 and 100")
        total = self.db.execute(
            "SELECT COUNT(*) FROM decisions WHERE name GLOB 'contract-transition/*' "
            "AND name NOT GLOB 'contract-transition/*/activated'"
        ).fetchone()[0]
        if total > 1000:
            raise StateError("contract transition review catalogue exceeds bound")
        rows = self.db.execute(
            "SELECT name,version FROM decisions WHERE name GLOB 'contract-transition/*' "
            "AND name NOT GLOB 'contract-transition/*/activated' "
            "ORDER BY name LIMIT 1001"
        ).fetchall()
        pending = []
        pending_count = 0
        participant_count = 0
        for row in rows:
            transition = self._single_contract_decision(row["name"])
            if transition is None or row["version"] != 1:
                raise StateError("contract decision history invalid")
            body = transition["body"]
            if type(body) is not dict:
                raise StateError("contract transition proposal invalid")
            body = self._validated_contract_transition(
                body, body.get("name"), body.get("to_version")
            )
            if self._contract_transition_key(body["name"], body["to_version"]) != row["name"]:
                raise StateError("contract transition identity invalid")
            current = self.db.execute(
                "SELECT MAX(version) FROM contracts WHERE name=?", (body["name"],)
            ).fetchone()[0]
            if current is None or current < body["from_version"]:
                raise StateError("contract transition source version is unavailable")
            participants = [body["producer_team"], *body["consumer_teams"]]
            participant_count += len(participants)
            if participant_count > 1000:
                raise StateError("contract transition review participants exceed bound")
            is_current_proposal = current == body["from_version"]
            if current >= body["to_version"]:
                activation = self._single_contract_decision(row["name"] + "/activated")
                published = self.db.execute(
                    "SELECT version,body FROM contracts WHERE name=? AND version=?",
                    (body["name"], body["to_version"]),
                ).fetchone()
                expected_activation = {
                    "kind": "contract_transition_activation",
                    "name": body["name"],
                    "from_version": body["from_version"],
                    "to_version": body["to_version"],
                    "contract_hash": body["contract_hash"],
                    "producer_task_id": body["producer_task_id"],
                    "acknowledged_teams": sorted(participants),
                }
                if (
                    activation is None
                    or activation["body"] != expected_activation
                    or published is None
                    or json.loads(published["body"]) != body["body"]
                    or hashlib.sha256(published["body"].encode("utf-8")).hexdigest()
                    != body["contract_hash"]
                ):
                    raise StateError("activated contract review history is inconsistent")
            for team in participants:
                ack_key = self._contract_ack_key(body["name"], body["to_version"], team)
                acknowledgement = self._single_contract_decision(ack_key)
                if acknowledgement is not None:
                    self._validated_contract_ack(
                        acknowledgement["body"],
                        body["name"],
                        body["to_version"],
                        team,
                        body["contract_hash"],
                    )
                    continue
                if not is_current_proposal:
                    if current >= body["to_version"]:
                        raise StateError("activated contract review evidence is incomplete")
                    continue
                pending_count += 1
                if len(pending) < limit:
                    pending.append(
                        {
                            "name": body["name"],
                            "from_version": body["from_version"],
                            "to_version": body["to_version"],
                            "contract_hash": body["contract_hash"],
                            "participant_team": team,
                            "participant_role": (
                                "producer" if team == body["producer_team"] else "consumer"
                            ),
                        }
                    )
        return {
            "items": pending,
            "pending_count": pending_count,
            "omitted_count": pending_count - len(pending),
        }

    @staticmethod
    def _validated_contract_transition(body: Any, name: str, version: int) -> dict:
        required = {
            "kind",
            "status",
            "name",
            "from_version",
            "to_version",
            "body",
            "contract_hash",
            "producer_task_id",
            "producer_team",
            "consumer_teams",
        }
        if type(body) is not dict or set(body) != required:
            raise StateError("contract transition proposal invalid")
        if (
            body["kind"] != "contract_transition"
            or body["status"] != "proposed"
            or type(body["name"]) is not str
            or body["name"] != name
            or type(body["from_version"]) is not int
            or body["from_version"] < 1
            or type(body["to_version"]) is not int
            or body["to_version"] != body["from_version"] + 1
            or body["to_version"] != version
            or type(body["producer_task_id"]) is not str
            or not body["producer_task_id"].strip()
            or type(body["producer_team"]) is not str
            or not body["producer_team"].strip()
            or type(body["contract_hash"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", body["contract_hash"])
        ):
            raise StateError("contract transition proposal invalid")
        teams = body["consumer_teams"]
        if (
            type(teams) is not list
            or not teams
            or len(teams) > 99
            or any(type(team) is not str or not team.strip() for team in teams)
            or teams != sorted(set(teams))
            or body["producer_team"] in teams
        ):
            raise StateError("contract transition participants invalid")
        try:
            detached_body = json.loads(_json(body["body"]))
            canonical = _json(detached_body)
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract transition content invalid") from None
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != body["contract_hash"]:
            raise StateError("contract transition content hash mismatch")
        if (
            type(detached_body) is dict
            and "version" in detached_body
            and (
                type(detached_body["version"]) is not int
                or detached_body["version"] != body["to_version"]
            )
        ):
            raise StateError("contract transition body version mismatch")
        return body

    @staticmethod
    def _validated_contract_ack(body: Any, name: str, version: int, team: str, digest: str) -> dict:
        expected = {
            "kind": "contract_acknowledgement",
            "name": name,
            "version": version,
            "team": team,
            "contract_hash": digest,
        }
        if (
            type(body) is not dict
            or set(body) != set(expected)
            or type(body.get("kind")) is not str
            or type(body.get("name")) is not str
            or type(body.get("version")) is not int
            or type(body.get("team")) is not str
            or type(body.get("contract_hash")) is not str
            or body != expected
        ):
            raise StateError("contract acknowledgement record invalid")
        return body

    def _put_contract_in_transaction(
        self, name: str, version: int, body: Any, body_json: str
    ) -> None:
        old = self.db.execute(
            "SELECT MAX(version) FROM contracts WHERE name=?", (name,)
        ).fetchone()[0]
        if old is not None and version <= old:
            raise StateError("contract versions must increase")
        self.db.execute("INSERT INTO contracts VALUES(?,?,?)", (name, version, body_json))
        self._event("contract_published", name, {"version": version, "body": body})

    @staticmethod
    def _contract_transition_key(name: str, version: int) -> str:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        return f"contract-transition/{digest}/{version}"

    @staticmethod
    def _contract_ack_key(name: str, version: int, team: str) -> str:
        contract_id = hashlib.sha256(name.encode("utf-8")).hexdigest()
        team_id = hashlib.sha256(team.encode("utf-8")).hexdigest()
        return f"contract-ack/{contract_id}/{version}/{team_id}"

    @staticmethod
    def _contract_review_key(request_id: str) -> str:
        if type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", request_id):
            raise StateError("contract review request identity invalid")
        return f"contract-review/{request_id}"

    @staticmethod
    def _contract_review_digest(value: Any) -> str:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()

    def _contract_review_replay_history_size(self, request_id: str) -> int:
        """Preflight retained bytes without hydrating JSON bodies."""
        key = self._contract_review_key(request_id)
        rows = self.db.execute(
            "SELECT version,length(CAST(body AS BLOB)) AS body_bytes FROM decisions "
            "WHERE name=? ORDER BY version LIMIT 6",
            (key,),
        ).fetchall()
        if len(rows) > 5 or [row["version"] for row in rows] != list(range(1, len(rows) + 1)):
            raise StateError("contract review history invalid")
        total = 0
        for row in rows:
            size = row["body_bytes"]
            if (
                type(size) is not int
                or size < 0
                or size > _CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES
            ):
                raise StateError("contract review replay history byte limit exceeded")
            total += size
        if total > _CONTRACT_REVIEW_REPLAY_REQUEST_MAX_BYTES:
            raise StateError("contract review replay request byte limit exceeded")
        return total

    def _contract_review_record(
        self, request_id: str, *, bounded_replay: bool = False
    ) -> dict | None:
        key = self._contract_review_key(request_id)
        if bounded_replay:
            self._contract_review_replay_history_size(request_id)
        rows = self.db.execute(
            "SELECT version,body,replaces,replaces_version FROM decisions "
            "WHERE name=? ORDER BY version",
            (key,),
        ).fetchall()
        if not rows:
            return None
        if [row["version"] for row in rows] != list(range(1, len(rows) + 1)):
            raise StateError("contract review history invalid")
        previous = None
        record = None
        allowed_status = {
            None: {"prepared"},
            "prepared": {"dispatching"},
            "dispatching": {"thread_known"},
            # Pre-custody terminal records remain readable. New finalization is
            # still gated on response_recorded in finish_contract_review().
            "thread_known": {"response_recorded", "approved", "blocked"},
            "response_recorded": {"approved", "blocked"},
        }
        for row in rows:
            body = json.loads(row["body"])
            if (
                type(body) is not dict
                or body.get("kind") != "contract_review_request"
                or body.get("request_id") != request_id
                or body.get("status")
                not in {
                    "prepared",
                    "dispatching",
                    "thread_known",
                    "response_recorded",
                    "approved",
                    "blocked",
                }
                or row["replaces"] != (key if previous is not None else None)
                or row["replaces_version"] != (row["version"] - 1 if previous is not None else None)
            ):
                raise StateError("contract review history invalid")
            if body["status"] not in allowed_status.get(
                previous.get("status") if previous is not None else None, set()
            ):
                raise StateError("contract review state transition invalid")
            if previous is not None and any(
                body.get(field) != previous.get(field)
                for field in (
                    "request_id",
                    "proposal_digest",
                    "contract_hash",
                    "name",
                    "version",
                    "team",
                    "role",
                    "evidence_revision",
                    "packet",
                    "packet_hash",
                    "dispatch_id",
                )
            ):
                raise StateError("contract review identity changed")
            if (
                previous is not None
                and "session" in previous
                and body.get("session") != previous["session"]
            ):
                raise StateError("contract review session identity changed")
            if (
                previous is not None
                and previous["status"]
                in {
                    "response_recorded",
                    "approved",
                    "blocked",
                }
                and any(
                    body.get(field) != previous.get(field)
                    for field in ("raw_sha256", "raw_result_base64", "provider_turn_id")
                )
            ):
                raise StateError("contract review response custody changed")
            if body["status"] == "prepared" and any(
                field in body
                for field in (
                    "session",
                    "raw_sha256",
                    "raw_result_base64",
                    "provider_turn_id",
                    "result",
                )
            ):
                raise StateError("contract review prepared state invalid")
            if body["status"] == "dispatching" and any(
                field in body
                for field in (
                    "session",
                    "raw_sha256",
                    "raw_result_base64",
                    "provider_turn_id",
                    "result",
                )
            ):
                raise StateError("contract review dispatch state invalid")
            if body["status"] == "thread_known":
                session = body.get("session")
                if (
                    type(session) is not dict
                    or set(session)
                    != {"thread_id", "model", "turn_dispatch_id", "process_identity"}
                    or any(
                        type(session[field]) is not str or not session[field]
                        for field in ("thread_id", "model", "turn_dispatch_id")
                    )
                    or type(session["process_identity"]) is not dict
                    or set(session["process_identity"]) != {"pid", "birth"}
                    or type(session["process_identity"]["pid"]) is not int
                    or session["process_identity"]["pid"] <= 0
                    or type(session["process_identity"]["birth"]) is not str
                    or "raw_sha256" in body
                    or "raw_result_base64" in body
                    or "provider_turn_id" in body
                    or "result" in body
                ):
                    raise StateError("contract review session state invalid")
            if body["status"] == "response_recorded" and (
                type(body.get("session")) is not dict
                or type(body.get("raw_sha256")) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", body["raw_sha256"])
                or type(body.get("raw_result_base64")) is not str
                or type(body.get("provider_turn_id")) is not str
                or not body["provider_turn_id"]
                or len(body["provider_turn_id"]) > 256
                or body["session"].get("turn_dispatch_id") != body.get("dispatch_id")
            ):
                raise StateError("contract review response custody invalid")
            if body["status"] in ("response_recorded", "approved", "blocked") and (
                "raw_result_base64" in body or "provider_turn_id" in body
            ):
                if (
                    type(body.get("session")) is not dict
                    or type(body.get("raw_sha256")) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", body["raw_sha256"])
                    or type(body.get("raw_result_base64")) is not str
                    or type(body.get("provider_turn_id")) is not str
                    or not body["provider_turn_id"]
                    or len(body["provider_turn_id"]) > 256
                    or body["session"].get("turn_dispatch_id") != body.get("dispatch_id")
                ):
                    raise StateError("contract review response custody invalid")
                try:
                    raw_result = base64.b64decode(body["raw_result_base64"], validate=True)
                except (ValueError, TypeError):
                    raise StateError("contract review response custody invalid") from None
                if (
                    len(raw_result) > 65536
                    or hashlib.sha256(raw_result).hexdigest() != body["raw_sha256"]
                ):
                    raise StateError("contract review response custody invalid")
            if body["status"] in ("approved", "blocked"):
                if (
                    type(body.get("raw_sha256")) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", body["raw_sha256"])
                    or type(body.get("result")) is not dict
                    or type(body.get("model_result")) is not dict
                    or type(body.get("blocking_reasons")) is not list
                    or (("raw_result_base64" in body) != ("provider_turn_id" in body))
                    or (
                        "raw_result_base64" not in body
                        and previous is not None
                        and previous["status"] != "thread_known"
                    )
                ):
                    raise StateError("contract review result state invalid")
            previous, record = body, {"name": key, "version": row["version"], "body": body}
        return record

    def prepare_contract_review(
        self, name: str, version: int, team: str, evidence_revision: str, packet: Any
    ) -> dict:
        """Persist one immutable, task-independent review request for exact evidence."""
        if (
            type(name) is not str
            or not name.strip()
            or type(version) is not int
            or version < 2
            or type(team) is not str
            or not team.strip()
            or type(evidence_revision) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", evidence_revision)
        ):
            raise StateError("contract review identity invalid")
        try:
            submitted = json.loads(_json(packet))
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract review packet invalid") from None
        required_packet = {
            "source_commit",
            "evidence_refs",
            "compatibility_analysis",
            "migration_plan",
        }
        if (
            type(submitted) is not dict
            or set(submitted) != required_packet
            or type(submitted["source_commit"]) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", submitted["source_commit"])
            or type(submitted["evidence_refs"]) is not list
            or not submitted["evidence_refs"]
            or len(submitted["evidence_refs"]) > 100
            or type(submitted["compatibility_analysis"]) is not str
            or not submitted["compatibility_analysis"].strip()
            or len(submitted["compatibility_analysis"]) > 8000
            or (
                submitted["migration_plan"] is not None
                and (
                    type(submitted["migration_plan"]) is not str
                    or not submitted["migration_plan"].strip()
                    or len(submitted["migration_plan"]) > 8000
                )
            )
        ):
            raise StateError("contract review packet shape invalid")
        refs = []
        for item in submitted["evidence_refs"]:
            if (
                type(item) is not dict
                or set(item) != {"ref", "sha256"}
                or type(item["ref"]) is not str
                or not item["ref"].strip()
                or len(item["ref"]) > 512
                or type(item["sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            ):
                raise StateError("contract review evidence reference invalid")
            refs.append(item["ref"])
        if len(set(refs)) != len(refs):
            raise StateError("contract review evidence references must be unique")
        key = self._contract_transition_key(name, version)
        with self._tx():
            transition = self._single_contract_decision(key)
            if transition is None:
                raise StateError("contract transition proposal missing")
            proposal = self._validated_contract_transition(transition["body"], name, version)
            current = self.db.execute(
                "SELECT MAX(version) FROM contracts WHERE name=?", (name,)
            ).fetchone()[0]
            if current != proposal["from_version"]:
                raise StateError("contract review proposal is no longer current")
            participants = [proposal["producer_team"], *proposal["consumer_teams"]]
            if team not in participants:
                raise StateError("contract review team is not a participant")
            producer_task_row = self.db.execute(
                "SELECT id FROM tasks WHERE id=?", (proposal["producer_task_id"],)
            ).fetchone()
            if producer_task_row is None:
                raise StateError("contract review producer task is unavailable")
            team_tasks = self.db.execute(
                "SELECT id FROM tasks WHERE team=? ORDER BY id LIMIT 101", (team,)
            ).fetchall()
            if len(team_tasks) > 100:
                raise StateError("contract review participant task inventory exceeds bound")
            task_inventory = [self._task(row["id"]) for row in team_tasks]
            prior_contract = self.db.execute(
                "SELECT version,body FROM contracts WHERE name=? AND version=?",
                (name, proposal["from_version"]),
            ).fetchone()
            if prior_contract is None:
                raise StateError("contract review source contract is unavailable")
            ack_key = self._contract_ack_key(name, version, team)
            ack = self._single_contract_decision(ack_key)
            if ack is not None:
                self._validated_contract_ack(
                    ack["body"], name, version, team, proposal["contract_hash"]
                )
                raise StateError("contract participant already acknowledged")
            proposal_digest = self._contract_review_digest(proposal)
            detached = {
                "name": name,
                "from_version": proposal["from_version"],
                "to_version": proposal["to_version"],
                "contract_hash": proposal["contract_hash"],
                "proposal_digest": proposal_digest,
                "participant_team": team,
                "participant_role": (
                    "producer" if team == proposal["producer_team"] else "consumer"
                ),
                "source_commit": submitted["source_commit"],
                "producer_task": self._task(proposal["producer_task_id"]),
                "old_contract": {
                    "version": prior_contract["version"],
                    "body": json.loads(prior_contract["body"]),
                },
                "proposed_contract": proposal["body"],
                "participant_tasks": task_inventory,
                "affected_consumer_task_ids": [task["id"] for task in task_inventory],
                "compatibility_analysis": submitted["compatibility_analysis"],
                "compatibility_evidence": submitted["evidence_refs"],
                "migration_plan": submitted["migration_plan"],
                "evidence_refs": refs,
            }
            packet_json = _json(detached)
            if len(packet_json.encode("utf-8")) > 65536:
                raise StateError("contract review packet exceeds byte bound")
            packet_hash = hashlib.sha256(packet_json.encode("utf-8")).hexdigest()
            request_id = self._contract_review_digest([proposal_digest, team, evidence_revision])
            existing = self._contract_review_record(request_id)
            if existing is not None:
                body = existing["body"]
                if (
                    body["proposal_digest"] != proposal_digest
                    or body["contract_hash"] != proposal["contract_hash"]
                    or body["packet_hash"] != packet_hash
                    or body["packet"] != detached
                ):
                    raise StateError("contract review request identity conflict")
                return existing
            body = {
                "kind": "contract_review_request",
                "status": "prepared",
                "request_id": request_id,
                "proposal_digest": proposal_digest,
                "contract_hash": proposal["contract_hash"],
                "name": name,
                "version": version,
                "team": team,
                "role": "producer" if team == proposal["producer_team"] else "consumer",
                "evidence_revision": evidence_revision,
                "packet": detached,
                "packet_hash": packet_hash,
                "dispatch_id": "contract-review:" + request_id,
            }
            self._put_decision_in_transaction(self._contract_review_key(request_id), body)
            return {"name": self._contract_review_key(request_id), "version": 1, "body": body}

    def _contract_review_currentness_in_snapshot(self, current: dict, packet_hash: str) -> dict:
        body = current["body"]
        if body["packet_hash"] != packet_hash:
            raise StateError("contract review packet changed")
        packet = body["packet"]
        if (
            type(packet) is not dict
            or set(packet)
            != {
                "name",
                "from_version",
                "to_version",
                "contract_hash",
                "proposal_digest",
                "participant_team",
                "participant_role",
                "source_commit",
                "producer_task",
                "old_contract",
                "proposed_contract",
                "participant_tasks",
                "affected_consumer_task_ids",
                "compatibility_analysis",
                "compatibility_evidence",
                "migration_plan",
                "evidence_refs",
            }
            or hashlib.sha256(_json(packet).encode("utf-8")).hexdigest() != packet_hash
        ):
            raise StateError("contract review packet custody invalid")
        transition = self._single_contract_decision(
            self._contract_transition_key(body["name"], body["version"])
        )
        if transition is None:
            raise StateError("contract transition proposal missing")
        proposal = self._validated_contract_transition(
            transition["body"], body["name"], body["version"]
        )
        expected_role = "producer" if body["team"] == proposal["producer_team"] else "consumer"
        if (
            body["role"] != expected_role
            or body["contract_hash"] != proposal["contract_hash"]
            or self._contract_review_digest(proposal) != body["proposal_digest"]
            or packet.get("name") != body["name"]
            or packet.get("to_version") != body["version"]
            or packet.get("from_version") != proposal["from_version"]
            or packet.get("contract_hash") != body["contract_hash"]
            or packet.get("proposed_contract") != proposal["body"]
            or packet.get("participant_team") != body["team"]
            or packet.get("participant_role") != expected_role
        ):
            raise StateError("contract review proposal changed")
        if (
            body["team"] not in [proposal["producer_team"], *proposal["consumer_teams"]]
            or packet["proposal_digest"] != body["proposal_digest"]
            or body["request_id"]
            != self._contract_review_digest(
                [body["proposal_digest"], body["team"], body["evidence_revision"]]
            )
        ):
            raise StateError("contract review request identity changed")
        blockers = []
        source = self.db.execute(
            "SELECT version,body FROM contracts WHERE name=? ORDER BY version DESC LIMIT 1",
            (body["name"],),
        ).fetchone()
        if source is None:
            raise StateError("contract review source contract is unavailable")
        if source["version"] != proposal["from_version"]:
            blockers.append("source_version_changed")
        pinned_source = packet.get("old_contract")
        if (
            type(pinned_source) is not dict
            or pinned_source.get("version") != proposal["from_version"]
        ):
            raise StateError("contract review source packet invalid")
        exact_source = self.db.execute(
            "SELECT version,body FROM contracts WHERE name=? AND version=?",
            (body["name"], proposal["from_version"]),
        ).fetchone()
        if exact_source is None:
            raise StateError("contract review source contract is unavailable")
        if json.loads(exact_source["body"]) != pinned_source.get("body"):
            blockers.append("source_contract_changed")
        producer = self._task(proposal["producer_task_id"])
        if producer["team"] != proposal["producer_team"] or producer != packet.get("producer_task"):
            blockers.append("producer_snapshot_changed")
        rows = self.db.execute(
            "SELECT id FROM tasks WHERE team=? ORDER BY id LIMIT 101", (body["team"],)
        ).fetchall()
        if len(rows) > 100:
            blockers.append("participant_inventory_exceeds_bound")
        else:
            inventory = [self._task(row["id"]) for row in rows]
            if inventory != packet.get("participant_tasks"):
                blockers.append("participant_inventory_changed")
        if [task["id"] for task in packet["participant_tasks"]] != packet.get(
            "affected_consumer_task_ids"
        ):
            raise StateError("contract review participant coverage invalid")
        ack_key = self._contract_ack_key(body["name"], body["version"], body["team"])
        acknowledgement = self._single_contract_decision(ack_key)
        if acknowledgement is not None:
            self._validated_contract_ack(
                acknowledgement["body"],
                body["name"],
                body["version"],
                body["team"],
                body["contract_hash"],
            )
            blockers.append("participant_already_acknowledged")
        return {
            "request_id": body["request_id"],
            "packet_hash": packet_hash,
            "journal_version": current["version"],
            "status": body["status"],
            "current": not blockers,
            "blockers": blockers,
        }

    def contract_review_currentness(self, request_id: str, packet_hash: str) -> dict:
        """Report packet evidence freshness without granting send authority."""
        if type(packet_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", packet_hash):
            raise StateError("contract review packet hash invalid")
        with self._read_snapshot():
            current = self._contract_review_record(request_id)
            if current is None:
                raise StateError("contract review request missing")
            return self._contract_review_currentness_in_snapshot(current, packet_hash)

    def contract_review_worklist(self, *, limit: int = 100) -> dict:
        """Project a bounded inventory of review requests without dispatch authority."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StateError("contract review worklist limit invalid")
        with self._read_snapshot():
            return self._contract_review_worklist_in_snapshot(limit=limit)

    def _contract_review_worklist_in_snapshot(
        self, *, limit: int, overflow_as_result: bool = False
    ) -> dict:
        """Build the bounded request projection inside an existing read snapshot."""
        rows = self.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'contract-review/*' "
            "GROUP BY name ORDER BY name LIMIT ?",
            (limit + 1,),
        ).fetchall()
        if len(rows) > limit:
            if overflow_as_result:
                return {
                    "items": [],
                    "count": None,
                    "limit": limit,
                    "dispatch_authority": False,
                    "overflow": True,
                }
            raise StateError("contract review worklist exceeds bound")
        items = []
        for row in rows:
            match = re.fullmatch(r"contract-review/([0-9a-f]{64})", row["name"])
            if match is None:
                raise StateError("contract review worklist identity invalid")
            current = self._contract_review_record(match.group(1))
            if current is None:
                raise StateError("contract review worklist record missing")
            body = current["body"]
            freshness = self._contract_review_currentness_in_snapshot(current, body["packet_hash"])
            items.append(
                {
                    "request_id": body["request_id"],
                    "journal_version": current["version"],
                    "status": body["status"],
                    "team": body["team"],
                    "role": body["role"],
                    "packet_hash": body["packet_hash"],
                    "current": freshness["current"],
                    "blockers": freshness["blockers"],
                    "dispatch_eligible": (body["status"] == "prepared" and freshness["current"]),
                }
            )
        return {
            "items": items,
            "count": len(items),
            "limit": limit,
            "dispatch_authority": False,
        }

    def _contract_review_global_replay_names_in_snapshot(self) -> list[str]:
        """Seek at most the bounded journal count without grouping retained versions."""
        names = []
        cursor = "contract-review/"
        query = _CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY
        while len(names) <= _CONTRACT_REVIEW_REPLAY_SCAN_LIMIT:
            row = self.db.execute(query, (cursor,)).fetchone()
            if row is None:
                break
            cursor = row["name"]
            names.append(cursor)
            query = _CONTRACT_REVIEW_GLOBAL_REPLAY_NEXT_QUERY
        return names

    def _contract_review_replay_inventory_in_snapshot(self, *, limit: int) -> dict:
        """Select only current custodied responses for bounded local replay."""
        names = self._contract_review_global_replay_names_in_snapshot()
        if len(names) > _CONTRACT_REVIEW_REPLAY_SCAN_LIMIT:
            return self._contract_review_replay_overflow()
        identities = []
        for name in names:
            match = re.fullmatch(r"contract-review/([0-9a-f]{64})", name)
            if match is None:
                raise StateError("contract review worklist identity invalid")
            identities.append(match.group(1))
        byte_total = 0
        try:
            for request_id in identities:
                byte_total += self._contract_review_replay_history_size(request_id)
                if byte_total > _CONTRACT_REVIEW_REPLAY_INVENTORY_MAX_BYTES:
                    return self._contract_review_replay_overflow()
        except StateError as exc:
            if "byte limit exceeded" in str(exc):
                return self._contract_review_replay_overflow()
            raise
        items = []
        skipped_count = 0
        for request_id in identities:
            current = self._contract_review_record(request_id)
            if current is None:
                raise StateError("contract review worklist record missing")
            body = current["body"]
            if body["status"] != "response_recorded":
                skipped_count += 1
                continue
            items.append(
                {
                    "request_id": body["request_id"],
                    "packet_hash": body["packet_hash"],
                }
            )
            if len(items) > limit:
                return self._contract_review_replay_overflow()
        return {
            "items": items,
            "count": len(items),
            "skipped_count": skipped_count,
            "overflow": False,
        }

    def _contract_review_transition_replay_inventory_in_snapshot(
        self, *, name: str, version: int, contract_hash: str, limit: int
    ) -> dict:
        """Select bounded response journals for one exact current proposal."""
        transition = self._single_contract_decision(self._contract_transition_key(name, version))
        if transition is None:
            raise StateError("contract transition proposal missing")
        proposal = self._validated_contract_transition(transition["body"], name, version)
        if proposal["contract_hash"] != contract_hash:
            raise StateError("contract review proposal changed")
        proposal_digest = self._contract_review_digest(proposal)
        names = self._contract_review_transition_replay_names_in_snapshot(
            name=name,
            version=version,
            contract_hash=contract_hash,
            proposal_digest=proposal_digest,
        )
        if len(names) > _CONTRACT_REVIEW_REPLAY_SCAN_LIMIT:
            return self._contract_review_replay_overflow()
        identities = []
        for journal_name in names:
            match = re.fullmatch(r"contract-review/([0-9a-f]{64})", journal_name)
            if match is None:
                raise StateError("contract review worklist identity invalid")
            identities.append(match.group(1))
        byte_total = 0
        try:
            for request_id in identities:
                byte_total += self._contract_review_replay_history_size(request_id)
                if byte_total > _CONTRACT_REVIEW_REPLAY_INVENTORY_MAX_BYTES:
                    return self._contract_review_replay_overflow()
        except StateError as exc:
            if "byte limit exceeded" in str(exc):
                return self._contract_review_replay_overflow()
            raise
        items = []
        skipped_count = 0
        for request_id in identities:
            current = self._contract_review_record(request_id)
            if current is None:
                raise StateError("contract review worklist record missing")
            body = current["body"]
            if (
                body["name"] != name
                or body["version"] != version
                or body["contract_hash"] != contract_hash
                or body["proposal_digest"] != proposal_digest
            ):
                raise StateError("contract review transition index mismatch")
            if body["status"] != "response_recorded":
                skipped_count += 1
                continue
            items.append({"request_id": body["request_id"], "packet_hash": body["packet_hash"]})
            if len(items) > limit:
                return self._contract_review_replay_overflow()
        return {
            "items": items,
            "count": len(items),
            "skipped_count": skipped_count,
            "overflow": False,
        }

    def _contract_review_transition_replay_names_in_snapshot(
        self, *, name: str, version: int, contract_hash: str, proposal_digest: str
    ) -> list[str]:
        """Seek distinct matching journal names without grouping their retained versions."""
        names = []
        query = _CONTRACT_REVIEW_TRANSITION_REPLAY_FIRST_QUERY
        params = (name, version, contract_hash, proposal_digest)
        cursor = None
        while len(names) <= _CONTRACT_REVIEW_REPLAY_SCAN_LIMIT:
            query_params = params if cursor is None else (*params, cursor)
            row = self.db.execute(query, query_params).fetchone()
            if row is None:
                break
            cursor = row["name"]
            names.append(cursor)
            query = _CONTRACT_REVIEW_TRANSITION_REPLAY_NEXT_QUERY
        return names

    @staticmethod
    def _contract_review_replay_overflow() -> dict:
        return {"items": [], "count": None, "skipped_count": None, "overflow": True}

    def begin_contract_review_dispatch(self, request_id: str, packet_hash: str) -> dict:
        """Write the one-shot dispatch intent before any metered provider call."""
        if type(packet_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", packet_hash):
            raise StateError("contract review packet hash invalid")
        with self._tx():
            current = self._contract_review_record(request_id)
            if current is None:
                raise StateError("contract review request missing")
            body = current["body"]
            if body["status"] != "prepared":
                raise StateError("contract review dispatch is not sendable")
            freshness = self._contract_review_currentness_in_snapshot(current, packet_hash)
            if not freshness["current"]:
                raise StateError(
                    "contract review packet is stale: " + ",".join(freshness["blockers"])
                )
            updated = {**body, "status": "dispatching"}
            self._put_decision_in_transaction(current["name"], updated)
            return {"name": current["name"], "version": current["version"] + 1, "body": updated}

    def record_contract_review_session(
        self,
        request_id: str,
        packet_hash: str,
        thread_id: str,
        model: str,
        turn_dispatch_id: str,
        process_identity: Any,
    ) -> dict:
        """Persist the exact metered session before issuing its review turn."""
        for value in (thread_id, model, turn_dispatch_id):
            _name(value)
        try:
            identity = json.loads(_json(process_identity))
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract review process identity invalid") from None
        if (
            type(identity) is not dict
            or set(identity) != {"pid", "birth"}
            or type(identity["pid"]) is not int
            or identity["pid"] <= 0
            or type(identity["birth"]) is not str
            or not identity["birth"]
        ):
            raise StateError("contract review process identity invalid")
        session = {
            "thread_id": thread_id,
            "model": model,
            "turn_dispatch_id": turn_dispatch_id,
            "process_identity": identity,
        }
        with self._tx():
            current = self._contract_review_record(request_id)
            if current is None:
                raise StateError("contract review request missing")
            body = current["body"]
            if body["packet_hash"] != packet_hash:
                raise StateError("contract review session is stale")
            if turn_dispatch_id != body["dispatch_id"]:
                raise StateError("contract review turn dispatch identity mismatch")
            if "session" in body:
                if body["session"] != session:
                    raise StateError("contract review session identity changed")
                return current
            if body["status"] != "dispatching":
                raise StateError("contract review session is stale")
            updated = {**body, "status": "thread_known", "session": session}
            self._put_decision_in_transaction(current["name"], updated)
            return {"name": current["name"], "version": current["version"] + 1, "body": updated}

    def record_contract_review_response(
        self,
        request_id: str,
        packet_hash: str,
        turn_dispatch_id: str,
        provider_turn_id: str,
        raw_result: bytes,
    ) -> dict:
        """Custody exact bounded review-result bytes before parsing or finalization.

        This is not the full RPC transcript and does not authenticate provider-turn or
        usage-ledger identity; the caller must qualify those separately.
        """
        if (
            type(packet_hash) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", packet_hash)
            or type(turn_dispatch_id) is not str
            or not turn_dispatch_id
            or type(provider_turn_id) is not str
            or not provider_turn_id
            or len(provider_turn_id) > 256
            or type(raw_result) is not bytes
            or len(raw_result) > 65536
        ):
            raise StateError("contract review response invalid")
        raw_sha256 = hashlib.sha256(raw_result).hexdigest()
        encoded = base64.b64encode(raw_result).decode("ascii")
        with self._tx():
            current = self._contract_review_record(request_id)
            if current is None:
                raise StateError("contract review request missing")
            body = current["body"]
            if body["packet_hash"] != packet_hash:
                raise StateError("contract review response is stale")
            if body["dispatch_id"] != turn_dispatch_id:
                raise StateError("contract review turn dispatch identity mismatch")
            response_identity = {
                "raw_sha256": raw_sha256,
                "raw_result_base64": encoded,
                "provider_turn_id": provider_turn_id,
            }
            if body["status"] in ("response_recorded", "approved", "blocked"):
                if any(body.get(key) != value for key, value in response_identity.items()):
                    raise StateError("contract review response custody changed")
                return current
            if body["status"] != "thread_known":
                raise StateError("contract review response has no durable session")
            session = body.get("session")
            if type(session) is not dict or session.get("turn_dispatch_id") != turn_dispatch_id:
                raise StateError("contract review response session mismatch")
            updated = {**body, "status": "response_recorded", **response_identity}
            self._put_decision_in_transaction(current["name"], updated)
            return {"name": current["name"], "version": current["version"] + 1, "body": updated}

    def get_contract_review_response(
        self, request_id: str, *, _bounded_replay: bool = False
    ) -> dict:
        """Read and rehash the immutable stored result bytes for local recovery."""
        with self._lock:
            current = self._contract_review_record(request_id, bounded_replay=_bounded_replay)
            if current is None or current["body"]["status"] not in {
                "response_recorded",
                "approved",
                "blocked",
            }:
                raise StateError("contract review response unavailable")
            body = current["body"]
            if "raw_result_base64" not in body:
                raise StateError("legacy contract review has no retained raw response")
            try:
                raw_result = base64.b64decode(body["raw_result_base64"], validate=True)
            except (ValueError, TypeError):
                raise StateError("contract review response custody invalid") from None
            if hashlib.sha256(raw_result).hexdigest() != body["raw_sha256"]:
                raise StateError("contract review response custody invalid")
            return {
                "request_id": request_id,
                "packet_hash": body["packet_hash"],
                "dispatch_id": body["dispatch_id"],
                "thread_id": body["session"]["thread_id"],
                "provider_turn_id": body["provider_turn_id"],
                "raw_sha256": body["raw_sha256"],
                "raw_result": raw_result,
            }

    def replay_contract_review_response(self, request_id: str, packet_hash: str) -> dict:
        """Finalize one already-custodied response locally, without contacting a provider."""
        if type(packet_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", packet_hash):
            raise StateError("contract review packet hash invalid")
        with self._read_snapshot():
            current = self._contract_review_record(request_id, bounded_replay=True)
            if current is None:
                raise StateError("contract review response unavailable")
            body = current["body"]
            if body["status"] not in {"response_recorded", "approved", "blocked"}:
                raise StateError("contract review response unavailable")
            if "raw_result_base64" not in body:
                raise StateError("legacy contract review has no retained raw response")
            try:
                raw_result = base64.b64decode(body["raw_result_base64"], validate=True)
            except (ValueError, TypeError):
                raise StateError("contract review response custody invalid") from None
            if hashlib.sha256(raw_result).hexdigest() != body["raw_sha256"]:
                raise StateError("contract review response custody invalid")
            response = {
                "request_id": request_id,
                "packet_hash": body["packet_hash"],
                "raw_sha256": body["raw_sha256"],
                "raw_result": raw_result,
            }
        if response["packet_hash"] != packet_hash:
            raise StateError("contract review response is stale")
        try:
            result = _strict_contract_review_result(response["raw_result"])
        except (TypeError, ValueError, UnicodeDecodeError, OverflowError, RecursionError):
            raise StateError("contract review response is not replayable") from None
        return self.finish_contract_review(
            request_id, packet_hash, response["raw_sha256"], result, _bounded_replay=True
        )

    def replay_pending_contract_review_responses(self, *, limit: int = 100) -> dict:
        """Locally finalize a bounded snapshot of custodied responses; never dispatch."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StateError("contract review replay limit invalid")
        with self._read_snapshot():
            inventory = self._contract_review_replay_inventory_in_snapshot(limit=limit)
        return self._replay_contract_review_inventory(inventory, limit=limit)

    def replay_contract_review_responses_for_transition(
        self, name: str, version: int, contract_hash: str, *, limit: int = 100
    ) -> dict:
        """Locally replay bounded custodied responses for one exact transition only."""
        if (
            type(name) is not str
            or not name.strip()
            or type(version) is not int
            or version < 2
            or type(contract_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", contract_hash) is None
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise StateError("contract review transition replay request invalid")
        with self._read_snapshot():
            inventory = self._contract_review_transition_replay_inventory_in_snapshot(
                name=name,
                version=version,
                contract_hash=contract_hash,
                limit=limit,
            )
        result = self._replay_contract_review_inventory(inventory, limit=limit)
        return {**result, "name": name, "version": version, "contract_hash": contract_hash}

    def _replay_contract_review_inventory(self, inventory: dict, *, limit: int) -> dict:
        if inventory["overflow"]:
            return {
                "limit": limit,
                "count": None,
                "overflow": True,
                "response_recorded_count": None,
                "replayed": [],
                "failures": [],
                "skipped_count": None,
                "dispatch_authority": False,
                "provider_dispatch": False,
            }

        response_recorded = inventory["items"]
        replayed = []
        failures = []
        for item in response_recorded:
            try:
                finished = self.replay_contract_review_response(
                    item["request_id"], item["packet_hash"]
                )
            except StateError as exc:
                error = str(exc)
                failures.append(
                    {
                        "request_id": item["request_id"],
                        "packet_hash": item["packet_hash"],
                        "error": error[:256],
                        "error_truncated": len(error) > 256,
                    }
                )
            else:
                body = finished["body"]
                replayed.append(
                    {
                        "request_id": item["request_id"],
                        "packet_hash": item["packet_hash"],
                        "status": body["status"],
                        "raw_sha256": body.get("raw_sha256"),
                    }
                )
        return {
            "limit": limit,
            "count": inventory["count"],
            "overflow": False,
            "response_recorded_count": len(response_recorded),
            "replayed": replayed,
            "failures": failures,
            "skipped_count": inventory["skipped_count"],
            "dispatch_authority": False,
            "provider_dispatch": False,
        }

    def finish_contract_review(
        self,
        request_id: str,
        packet_hash: str,
        raw_sha256: str,
        result: Any,
        *,
        _bounded_replay: bool = False,
    ) -> dict:
        """Commit immutable review evidence and any exact acknowledgement atomically."""
        if (
            type(packet_hash) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", packet_hash)
            or type(raw_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256)
        ):
            raise StateError("contract review evidence hash invalid")
        try:
            detached = json.loads(_json(result))
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract review result invalid") from None
        required = {
            "decision",
            "compatibility",
            "rationale",
            "findings",
            "reviewed_evidence_refs",
            "affected_consumers_reviewed",
            "migration_plan",
        }
        if (
            type(detached) is not dict
            or set(detached) != required
            or detached["decision"] not in ("approve", "blocked")
            or detached["compatibility"] not in ("compatible", "migration_required", "insufficient")
            or type(detached["rationale"]) is not str
            or not detached["rationale"].strip()
            or type(detached["findings"]) is not list
            or any(type(item) is not str for item in detached["findings"])
            or type(detached["reviewed_evidence_refs"]) is not list
            or any(type(item) is not str for item in detached["reviewed_evidence_refs"])
            or type(detached["affected_consumers_reviewed"]) is not list
            or any(type(item) is not str for item in detached["affected_consumers_reviewed"])
            or (
                detached["migration_plan"] is not None
                and type(detached["migration_plan"]) is not str
            )
        ):
            raise StateError("contract review result shape invalid")
        if (
            len(_json(detached).encode("utf-8")) > 32768
            or len(detached["rationale"]) > 4000
            or len(detached["findings"]) > 100
            or any(len(item) > 1000 for item in detached["findings"])
            or len(detached["reviewed_evidence_refs"]) > 100
            or any(len(item) > 512 for item in detached["reviewed_evidence_refs"])
            or len(detached["affected_consumers_reviewed"]) > 100
            or any(len(item) > 32768 for item in detached["affected_consumers_reviewed"])
            or (detached["migration_plan"] is not None and len(detached["migration_plan"]) > 8000)
        ):
            raise StateError("contract review result exceeds bound")
        with self._tx():
            current = self._contract_review_record(request_id, bounded_replay=_bounded_replay)
            if current is None:
                raise StateError("contract review request missing")
            body = current["body"]
            if body["packet_hash"] != packet_hash:
                raise StateError("contract review packet changed")
            if body["status"] in ("approved", "blocked"):
                completed = body.get("model_result")
                if body.get("raw_sha256") != raw_sha256 or completed != detached:
                    raise StateError("contract review result is immutable")
                return current
            if body["status"] != "response_recorded":
                raise StateError("contract review response is not durably recorded")
            if body.get("raw_sha256") != raw_sha256:
                raise StateError("contract review response hash mismatch")
            response = self.get_contract_review_response(
                request_id, _bounded_replay=_bounded_replay
            )
            try:
                raw_result = _strict_contract_review_result(response["raw_result"])
                if _json(raw_result) != _json(detached):
                    raise ValueError("stored response differs from parsed result")
            except (TypeError, ValueError, UnicodeDecodeError, OverflowError, RecursionError):
                raise StateError(
                    "contract review raw result does not match parsed result"
                ) from None
            allowed_refs = body["packet"].get("evidence_refs", [])
            allowed_consumers = body["packet"].get("affected_consumer_task_ids", [])
            if (
                type(allowed_refs) is not list
                or any(type(ref) is not str for ref in allowed_refs)
                or type(allowed_consumers) is not list
                or any(type(task_id) is not str for task_id in allowed_consumers)
                or len(set(detached["reviewed_evidence_refs"]))
                != len(detached["reviewed_evidence_refs"])
                or not set(detached["reviewed_evidence_refs"]).issubset(allowed_refs)
                or len(set(detached["affected_consumers_reviewed"]))
                != len(detached["affected_consumers_reviewed"])
                or not set(detached["affected_consumers_reviewed"]).issubset(allowed_consumers)
            ):
                raise StateError("contract review evidence coverage invalid")
            approved = detached["decision"] == "approve"
            if approved and (
                sorted(detached["affected_consumers_reviewed"]) != sorted(allowed_consumers)
                or detached["compatibility"] == "insufficient"
                or not detached["reviewed_evidence_refs"]
                or (
                    detached["compatibility"] == "migration_required"
                    and not (detached["migration_plan"] or "").strip()
                )
            ):
                raise StateError("contract review approval lacks compatibility evidence")
            freshness = self._contract_review_currentness_in_snapshot(current, packet_hash)
            blocker_messages = {
                "participant_inventory_exceeds_bound": (
                    "participant task inventory exceeds bound after packet preparation"
                ),
                "participant_inventory_changed": (
                    "participant task inventory changed after packet preparation"
                ),
                "producer_snapshot_changed": (
                    "producer task evidence changed after packet preparation"
                ),
                "source_version_changed": (
                    "contract source version changed while this review was pending"
                ),
                "source_contract_changed": (
                    "contract source body changed while this review was pending"
                ),
                "participant_already_acknowledged": (
                    "participant was acknowledged while this review was pending"
                ),
            }
            stale_reasons = [blocker_messages.get(code, code) for code in freshness["blockers"]]
            transition = self._single_contract_decision(
                self._contract_transition_key(body["name"], body["version"])
            )
            if transition is None:
                raise StateError("contract transition proposal missing")
            proposal = self._validated_contract_transition(
                transition["body"], body["name"], body["version"]
            )
            current_version = self.db.execute(
                "SELECT MAX(version) FROM contracts WHERE name=?", (body["name"],)
            ).fetchone()[0]
            ack_key = self._contract_ack_key(body["name"], body["version"], body["team"])
            updated = {
                **body,
                "status": "approved" if approved and not stale_reasons else "blocked",
                "raw_sha256": raw_sha256,
                "model_result": detached,
                "result": detached,
                "blocking_reasons": stale_reasons,
            }
            self._put_decision_in_transaction(current["name"], updated)
            if approved and not stale_reasons:
                if (
                    self._contract_review_digest(proposal) != body["proposal_digest"]
                    or proposal["contract_hash"] != body["contract_hash"]
                    or body["team"] not in [proposal["producer_team"], *proposal["consumer_teams"]]
                    or current_version != proposal["from_version"]
                ):
                    raise StateError("contract review proposal changed")
                existing = self._single_contract_decision(ack_key)
                ack_record = {
                    "kind": "contract_acknowledgement",
                    "name": body["name"],
                    "version": body["version"],
                    "team": body["team"],
                    "contract_hash": body["contract_hash"],
                }
                if existing is not None:
                    raise StateError("contract participant acknowledgement race")
                self._put_decision_in_transaction(ack_key, ack_record)
            return {"name": current["name"], "version": current["version"] + 1, "body": updated}

    def propose_contract_transition(
        self,
        name: str,
        from_version: int,
        to_version: int,
        body: Any,
        *,
        producer_task_id: str,
        producer_team: str,
        consumer_teams: list[str],
    ) -> dict:
        """Persist an immutable contract proposal and its required producer/consumer set."""
        if type(name) is not str or not name.strip():
            raise StateError("contract name must be a plain nonempty string")
        if any(type(v) is not int or v < 1 for v in (from_version, to_version)):
            raise StateError("contract versions must be positive integers")
        if to_version != from_version + 1:
            raise StateError("contract transition must advance exactly one version")
        if type(producer_task_id) is not str or not producer_task_id.strip():
            raise StateError("contract producer task required")
        if type(producer_team) is not str or not producer_team.strip():
            raise StateError("contract producer team required")
        if type(consumer_teams) is not list or any(
            type(team) is not str or not team.strip() for team in consumer_teams
        ):
            raise StateError("contract consumer teams must be nonempty strings")
        consumer_teams = list(consumer_teams)
        if not consumer_teams:
            raise StateError("contract transition requires at least one consumer")
        if len(set(consumer_teams)) != len(consumer_teams) or producer_team in consumer_teams:
            raise StateError("contract participants must be unique")
        try:
            body = json.loads(_json(body))
            body_json = _json(body)
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract body must be finite JSON data") from None
        key = self._contract_transition_key(name, to_version)
        contract_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
        record = {
            "kind": "contract_transition",
            "status": "proposed",
            "name": name,
            "from_version": from_version,
            "to_version": to_version,
            "body": body,
            "contract_hash": contract_hash,
            "producer_task_id": producer_task_id,
            "producer_team": producer_team,
            "consumer_teams": sorted(consumer_teams),
        }
        self._validated_contract_transition(record, name, to_version)
        with self._tx():
            existing = self._single_contract_decision(key)
            if existing is not None:
                existing_body = self._validated_contract_transition(
                    existing["body"], name, to_version
                )
                if existing_body != record:
                    raise StateError("contract transition proposal is immutable")
                return {
                    "name": existing["name"],
                    "version": existing["version"],
                    "body": existing_body,
                }
            current = self.db.execute(
                "SELECT MAX(version) FROM contracts WHERE name=?", (name,)
            ).fetchone()[0]
            if current != from_version:
                raise StateError("contract transition source version is stale")
            version = self._put_decision_in_transaction(key, record)
            return {"name": key, "version": version, "body": record}

    def get_contract_transition(self, name: str, version: int) -> dict | None:
        if type(name) is not str or type(version) is not int:
            raise StateError("invalid contract transition identity")
        return self.get_decision(self._contract_transition_key(name, version))

    def acknowledge_contract_transition(
        self, name: str, version: int, team: str, contract_hash: str
    ) -> dict:
        """Record one immutable, content-bound acknowledgement for a required participant."""
        if type(name) is not str or not name.strip() or type(team) is not str or not team.strip():
            raise StateError("contract and team names must be plain nonempty strings")
        if type(version) is not int or version < 2:
            raise StateError("contract transition version must be an integer >= 2")
        if type(contract_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", contract_hash):
            raise StateError("contract hash must be lowercase SHA-256")
        key = self._contract_transition_key(name, version)
        ack_key = self._contract_ack_key(name, version, team)
        with self._tx():
            transition = self._single_contract_decision(key)
            if transition is None:
                raise StateError("contract transition proposal missing")
            proposal = self._validated_contract_transition(transition["body"], name, version)
            if proposal["contract_hash"] != contract_hash or team not in [
                proposal["producer_team"],
                *proposal["consumer_teams"],
            ]:
                raise StateError("contract acknowledgement does not match proposal")
            record = {
                "kind": "contract_acknowledgement",
                "name": name,
                "version": version,
                "team": team,
                "contract_hash": contract_hash,
            }
            existing = self._single_contract_decision(ack_key)
            if existing is not None:
                self._validated_contract_ack(existing["body"], name, version, team, contract_hash)
                if existing["body"] != record:
                    raise StateError("contract acknowledgement is immutable")
                return {"name": existing["name"], "version": existing["version"], "body": record}
            ack_version = self._put_decision_in_transaction(ack_key, record)
            return {"name": ack_key, "version": ack_version, "body": record}

    def activate_contract_transition(self, name: str, version: int) -> dict:
        """Atomically publish a proposed version after producer integration and all exact acks."""
        if type(name) is not str or not name.strip() or type(version) is not int or version < 2:
            raise StateError("invalid contract transition identity")
        key = self._contract_transition_key(name, version)
        result_key = key + "/activated"
        with self._tx():
            transition = self._single_contract_decision(key)
            if transition is None:
                raise StateError("contract transition proposal missing")
            proposal = self._validated_contract_transition(transition["body"], name, version)
            prior = self._single_contract_decision(result_key)
            if prior is not None:
                task = self.db.execute(
                    "SELECT team,state FROM tasks WHERE id=?", (proposal["producer_task_id"],)
                ).fetchone()
                participants = [proposal["producer_team"], *proposal["consumer_teams"]]
                for team in participants:
                    ack = self._single_contract_decision(
                        self._contract_ack_key(name, version, team)
                    )
                    if ack is None:
                        raise StateError("contract activation evidence incomplete")
                    self._validated_contract_ack(
                        ack["body"], name, version, team, proposal["contract_hash"]
                    )
                published = self.db.execute(
                    "SELECT version,body FROM contracts WHERE name=? AND version=?",
                    (name, version),
                ).fetchone()
                expected_result = {
                    "kind": "contract_transition_activation",
                    "name": name,
                    "from_version": proposal["from_version"],
                    "to_version": version,
                    "contract_hash": proposal["contract_hash"],
                    "producer_task_id": proposal["producer_task_id"],
                    "acknowledged_teams": sorted(participants),
                }
                if (
                    published is None
                    or task is None
                    or task["team"] != proposal["producer_team"]
                    or task["state"] != "integrated"
                    or json.loads(published["body"]) != proposal["body"]
                    or prior["body"] != expected_result
                    or prior["body"].get("contract_hash")
                    != hashlib.sha256(published["body"].encode("utf-8")).hexdigest()
                ):
                    raise StateError("activated contract state is inconsistent")
                return {
                    "name": prior["name"],
                    "version": prior["version"],
                    "body": prior["body"],
                    "created": False,
                }
            current = self.db.execute(
                "SELECT MAX(version) FROM contracts WHERE name=?", (name,)
            ).fetchone()[0]
            if current != proposal["from_version"] or version != proposal["to_version"]:
                raise StateError("contract transition source version is stale")
            task = self.db.execute(
                "SELECT team,state FROM tasks WHERE id=?", (proposal.get("producer_task_id"),)
            ).fetchone()
            if (
                task is None
                or task["team"] != proposal.get("producer_team")
                or task["state"] != "integrated"
            ):
                raise StateError("contract producer is not integrated")
            participants = [proposal["producer_team"], *proposal["consumer_teams"]]
            digest = proposal["contract_hash"]
            for team in participants:
                ack_key = self._contract_ack_key(name, version, team)
                ack = self._single_contract_decision(ack_key)
                if ack is None:
                    raise StateError("contract transition acknowledgements incomplete")
                self._validated_contract_ack(ack["body"], name, version, team, digest)
            body_json = _json(proposal["body"])
            self._put_contract_in_transaction(name, version, proposal["body"], body_json)
            result = {
                "kind": "contract_transition_activation",
                "name": name,
                "from_version": proposal["from_version"],
                "to_version": version,
                "contract_hash": digest,
                "producer_task_id": proposal["producer_task_id"],
                "acknowledged_teams": sorted(participants),
            }
            result_version = self._put_decision_in_transaction(result_key, result)
            return {"name": result_key, "version": result_version, "body": result, "created": True}

    def get_contract(self, name: str, version: int | None = None) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM contracts WHERE name=? AND (? IS NULL OR version=?) "
                "ORDER BY version DESC LIMIT 1",
                (name, version, version),
            ).fetchone()
            return None if row is None else {**dict(row), "body": json.loads(row["body"])}

    def put_decision(self, name: str, body: Any, replaces: str | None = None) -> int:
        if type(name) is not str:
            raise StateError("decision name must be a plain string")
        _name(name)
        if (
            name == "lifecycle/owner"
            or name.startswith("lifecycle/request/")
            or name.startswith("contract-transition/")
            or name.startswith("contract-ack/")
            or name.startswith("contract-review/")
            or name.startswith("sol-handoff/")
        ):
            raise StateError("decision_namespace_reserved")
        with self._tx():
            old = (
                self.db.execute(
                    "SELECT MAX(version) FROM decisions WHERE name=?", (name,)
                ).fetchone()[0]
                or 0
            )
            # Pin the replaced version; later edits of that decision cannot rewrite history.
            replaced = (
                self.get_decision(replaces) if replaces is not None else self.get_decision(name)
            )
            if replaces is not None and replaced is None:
                raise StateError("replacement decision must exist")
            replaces = replaced["name"] if replaced else None
            replaces_version = replaced["version"] if replaced else None
            self.db.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?)",
                (name, old + 1, _json(body), replaces, replaces_version),
            )
            self._event(
                "decision_recorded",
                name,
                {
                    "version": old + 1,
                    "body": body,
                    "replaces": replaces,
                    "replaces_version": replaces_version,
                },
            )
            return old + 1

    def _put_decision_in_transaction(self, name: str, body: Any) -> int:
        old = (
            self.db.execute("SELECT MAX(version) FROM decisions WHERE name=?", (name,)).fetchone()[
                0
            ]
            or 0
        )
        replaced = self.get_decision(name)
        version = old + 1
        self.db.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?)",
            (
                name,
                version,
                _json(body),
                name if replaced else None,
                replaced["version"] if replaced else None,
            ),
        )
        self._event(
            "decision_recorded",
            name,
            {
                "version": version,
                "body": body,
                "replaces": name if replaced else None,
                "replaces_version": replaced["version"] if replaced else None,
            },
        )
        return version

    def _lifecycle_now(self) -> float:
        now = self.clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise StateError("lifecycle_clock_invalid")
        try:
            finite_now = math.isfinite(now)
        except OverflowError:
            finite_now = False
        if not finite_now:
            raise StateError("lifecycle_clock_invalid")
        return float(now)

    def _lifecycle_decision_versions(self, name: str) -> list[int]:
        rows = self.db.execute(
            "SELECT version FROM decisions WHERE name=? ORDER BY version", (name,)
        ).fetchall()
        versions = [row[0] for row in rows]
        if versions != list(range(1, len(versions) + 1)):
            raise StateError("lifecycle_decision_history_invalid")
        return versions

    def _lifecycle_owner_history_head(self) -> int | None:
        """Validate owner version continuity with constant Python-side memory."""
        row = self.db.execute(
            "SELECT COUNT(*) AS version_count, MIN(version) AS first_version, "
            "MAX(version) AS last_version, "
            "SUM(CASE WHEN typeof(version)='integer' THEN 0 ELSE 1 END) AS invalid_versions "
            "FROM decisions WHERE name='lifecycle/owner'"
        ).fetchone()
        count = row["version_count"]
        if type(count) is not int:
            raise StateError("lifecycle_decision_history_invalid")
        if count == 0:
            return None
        first, last = row["first_version"], row["last_version"]
        if (
            row["invalid_versions"] != 0
            or type(first) is not int
            or type(last) is not int
            or first != 1
            or last != count
        ):
            raise StateError("lifecycle_decision_history_invalid")
        return last

    def _lifecycle_fence(self, lease: Any) -> dict:
        current = self.get_decision("lifecycle/owner")
        head = self._lifecycle_owner_history_head()
        if (current is None) != (head is None) or (
            current is not None and current["version"] != head
        ):
            raise StateError("lifecycle_decision_history_invalid")
        body = self._lifecycle_fence_state(current["body"]) if current is not None else None
        now = self._lifecycle_now()
        if (
            type(lease) is not dict
            or current is None
            or type(lease.get("owner")) is not str
            or type(lease.get("epoch")) is not int
            or body["status"] != "active"
            or body["owner"] != lease.get("owner")
            or body["epoch"] != lease.get("epoch")
            or body["lease_until"] <= now
        ):
            raise StateError("stale_lifecycle_owner")
        return body

    def claim_lifecycle_owner(self, owner: str, lease_seconds: float = 30) -> dict | None:
        """Claim the passive lifecycle journal using a durable, monotonically fenced epoch.

        This does not schedule work or start a process. A live owner cannot be displaced;
        an expired or released owner can be replaced only by a strictly newer epoch.
        """
        _name(owner)
        if len(owner) > 200:
            raise StateError("lifecycle_owner_invalid")
        lease_seconds = _positive(lease_seconds)
        with self._tx():
            current = self.get_decision("lifecycle/owner")
            head = self._lifecycle_owner_history_head()
            if (current is None) != (head is None) or (
                current is not None and current["version"] != head
            ):
                raise StateError("lifecycle_decision_history_invalid")
            now = self._lifecycle_now()
            if current is not None:
                old = self._lifecycle_fence_state(current["body"])
            else:
                old = None
            if old and old["status"] == "active" and old["lease_until"] > now:
                return None
            epoch = (old["epoch"] if old else 0) + 1
            body = {
                "status": "active",
                "owner": owner,
                "epoch": epoch,
                "lease_until": now + lease_seconds,
            }
            version = self._put_decision_in_transaction("lifecycle/owner", body)
            return {**body, "version": version}

    @staticmethod
    def _lifecycle_fence_state(body: Any) -> dict:
        if (
            type(body) is not dict
            or set(body) != {"status", "owner", "epoch", "lease_until"}
            or type(body["status"]) is not str
            or (body["status"] != "active" and body["status"] != "released")
            or type(body["owner"]) is not str
            or not body["owner"].strip()
            or len(body["owner"]) > 200
            or type(body["epoch"]) is not int
            or body["epoch"] <= 0
            or isinstance(body["lease_until"], bool)
            or not isinstance(body["lease_until"], (int, float))
        ):
            raise StateError("lifecycle_owner_state_invalid")
        try:
            finite_lease = math.isfinite(body["lease_until"])
        except OverflowError:
            finite_lease = False
        if not finite_lease:
            raise StateError("lifecycle_owner_state_invalid")
        return body

    def renew_lifecycle_owner(self, lease: Any, lease_seconds: float = 30) -> dict:
        """Extend a current lifecycle owner lease without changing its fencing epoch."""
        lease = _lifecycle_snapshot(lease)
        lease_seconds = _positive(lease_seconds)
        with self._tx():
            current = self._lifecycle_fence(lease)
            body = {**current, "lease_until": self._lifecycle_now() + lease_seconds}
            version = self._put_decision_in_transaction("lifecycle/owner", body)
            return {**body, "version": version}

    def release_lifecycle_owner(self, lease: Any) -> None:
        """Persist owner release; a later claim receives a higher epoch."""
        lease = _lifecycle_snapshot(lease)
        with self._tx():
            current = self._lifecycle_fence(lease)
            body = {**current, "status": "released", "lease_until": self._lifecycle_now()}
            self._put_decision_in_transaction("lifecycle/owner", body)

    def record_lifecycle_plan(self, lease: Any, plan: Any) -> dict:
        """Persist a non-launch lifecycle decision under the current owner fence.

        Launch requests require `record_lifecycle_evaluation` so their claimed gate inputs
        are stored beside the deterministic plan. This API has no process, service,
        scheduler, or dispatch side effects. Exact retries return the original journal identity.
        """
        lease = _lifecycle_snapshot(lease)
        plan = _lifecycle_snapshot(plan)
        required = {
            "action",
            "reason",
            "retry_at",
            "attempt_id",
            "request_owner",
            "request_epoch",
            "decision_sha256",
        }
        if type(plan) is not dict or set(plan) != required:
            raise StateError("invalid_lifecycle_plan")
        if type(plan["action"]) is not str or plan["action"] not in {
            "hold",
            "wait",
            "observe",
            "recovery_required",
            "launch_request",
        }:
            raise StateError("invalid_lifecycle_plan")
        if plan["action"] == "launch_request":
            raise StateError("lifecycle_launch_requires_observation")
        if (
            type(plan["reason"]) is not str
            or not plan["reason"].strip()
            or len(plan["reason"]) > 500
        ):
            raise StateError("invalid_lifecycle_plan")
        if plan["retry_at"] is not None:
            if isinstance(plan["retry_at"], bool) or not isinstance(plan["retry_at"], (int, float)):
                raise StateError("invalid_lifecycle_plan")
            try:
                finite_retry_at = math.isfinite(plan["retry_at"])
            except OverflowError:
                finite_retry_at = False
            if not finite_retry_at:
                raise StateError("invalid_lifecycle_plan")
        if plan["attempt_id"] is not None and (
            type(plan["attempt_id"]) is not int or plan["attempt_id"] <= 0
        ):
            raise StateError("invalid_lifecycle_plan")
        if plan["request_owner"] is not None:
            _name(plan["request_owner"])
        if plan["request_epoch"] is not None and (
            type(plan["request_epoch"]) is not int or plan["request_epoch"] <= 0
        ):
            raise StateError("invalid_lifecycle_plan")
        if plan["action"] != "launch_request" and (
            plan["request_owner"] is not None or plan["request_epoch"] is not None
        ):
            raise StateError("invalid_lifecycle_plan")
        digest = plan["decision_sha256"]
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise StateError("invalid_lifecycle_plan")

        with self._tx():
            owner = self._lifecycle_fence(lease)
            return self._persist_lifecycle_evaluation(owner, plan)

    def record_lifecycle_evaluation(self, lease: Any, observations: Any) -> dict:
        """Compute and persist a plan together with the exact caller-supplied observations.

        The observation object is detached before a possible SQLite lock wait. The planner
        runs under the current durable owner fence, and launch requests can only be emitted
        from that computed plan. Stored observations are provenance for the decision inputs,
        not proof that an external sensor measured them correctly.
        """
        lease = _lifecycle_snapshot(lease)
        observations = _lifecycle_snapshot(observations)
        if type(observations) is not dict or set(observations) != _LIFECYCLE_OBSERVATION_FIELDS:
            raise StateError("invalid_lifecycle_observation")

        with self._tx():
            owner = self._lifecycle_fence(lease)
            bound_observations = {
                **observations,
                "request_owner": owner["owner"],
                "request_epoch": owner["epoch"],
            }
            from .lifecycle import plan_lifecycle

            computed = plan_lifecycle(**bound_observations)
            plan = {
                "action": computed.action,
                "reason": computed.reason,
                "retry_at": computed.retry_at,
                "attempt_id": computed.attempt_id,
                "request_owner": computed.request_owner,
                "request_epoch": computed.request_epoch,
                "decision_sha256": computed.decision_sha256,
            }
            if plan["action"] == "launch_request":
                self._require_controller_parse_recovery_clear()
            return self._persist_lifecycle_evaluation(owner, plan, bound_observations)

    def _require_controller_parse_recovery_clear(self) -> None:
        """Reject optimistic launch requests while the durable parser recovery is open.

        This checks only the Store-owned controller-parse obligation. It does not
        establish that other lifecycle gates are current or complete.
        """
        circuit_name = "circuit/controller-parse"
        obligation_name = "recovery-obligation/controller-parse"
        circuit_versions = self._lifecycle_decision_versions(circuit_name)
        obligation_versions = self._lifecycle_decision_versions(obligation_name)
        if not circuit_versions and not obligation_versions:
            return
        if not circuit_versions or circuit_versions != obligation_versions:
            raise StateError("controller_parse_recovery_history_invalid")

        def valid_open_pair(circuit_body: Any, obligation_body: Any) -> bool:
            circuit_fields = {
                "status",
                "fault",
                "first_attempt_id",
                "task_id",
                "stage",
                "exception_type",
            }
            obligation_fields = {
                "status",
                "fault",
                "first_attempt_id",
                "task_id",
                "stage",
                "required_evidence",
            }
            return (
                type(circuit_body) is dict
                and set(circuit_body) == circuit_fields
                and circuit_body["status"] == "open"
                and circuit_body["fault"] == "structured_response"
                and type(circuit_body["first_attempt_id"]) is int
                and circuit_body["first_attempt_id"] > 0
                and type(circuit_body["task_id"]) is str
                and bool(circuit_body["task_id"].strip())
                and type(circuit_body["stage"]) is str
                and bool(circuit_body["stage"].strip())
                and type(circuit_body["exception_type"]) is str
                and bool(circuit_body["exception_type"].strip())
                and type(obligation_body) is dict
                and set(obligation_body) == obligation_fields
                and obligation_body["status"] == "open"
                and obligation_body["fault"] == circuit_body["fault"]
                and type(obligation_body["first_attempt_id"]) is int
                and obligation_body["first_attempt_id"] == circuit_body["first_attempt_id"]
                and type(obligation_body["task_id"]) is str
                and obligation_body["task_id"] == circuit_body["task_id"]
                and type(obligation_body["stage"]) is str
                and obligation_body["stage"] == circuit_body["stage"]
                and obligation_body["required_evidence"]
                == [
                    "qualified parser/controller fix",
                    "independent release review",
                    "separate bounded recovery authorization",
                ]
            )

        def valid_resolved_pair(circuit_body: Any, obligation_body: Any) -> bool:
            circuit_fields = {
                "status",
                "recovery_id",
                "fix_sha256",
                "review_ref",
                "authorization_ref",
                "fault",
            }
            obligation_fields = {
                "status",
                "recovery_id",
                "fix_sha256",
                "review_ref",
                "authorization_ref",
            }
            return (
                type(circuit_body) is dict
                and set(circuit_body) == circuit_fields
                and circuit_body["status"] == "resolved"
                and circuit_body["fault"] == "structured_response"
                and type(obligation_body) is dict
                and set(obligation_body) == obligation_fields
                and obligation_body["status"] == "resolved"
                and all(
                    type(circuit_body[key]) is str and bool(circuit_body[key].strip())
                    for key in ("recovery_id", "fix_sha256", "review_ref", "authorization_ref")
                )
                and re.fullmatch(r"[0-9a-f]{64}", circuit_body["fix_sha256"]) is not None
                and all(
                    obligation_body[key] == circuit_body[key]
                    for key in ("recovery_id", "fix_sha256", "review_ref", "authorization_ref")
                )
            )

        previous_status = None
        for version in circuit_versions:
            circuit = self.get_decision(circuit_name, version)
            obligation = self.get_decision(obligation_name, version)
            if circuit is None or obligation is None:
                raise StateError("controller_parse_recovery_history_invalid")
            circuit_body = circuit["body"]
            obligation_body = obligation["body"]
            if type(circuit_body) is not dict or type(obligation_body) is not dict:
                raise StateError("controller_parse_recovery_history_invalid")
            status = circuit_body.get("status")
            if status == "open":
                if previous_status not in (None, "resolved") or not valid_open_pair(
                    circuit_body, obligation_body
                ):
                    raise StateError("controller_parse_recovery_history_invalid")
            elif status == "resolved":
                if previous_status != "open" or not valid_resolved_pair(
                    circuit_body, obligation_body
                ):
                    raise StateError("controller_parse_recovery_history_invalid")
            else:
                raise StateError("controller_parse_recovery_history_invalid")
            previous_status = status

        if previous_status == "open":
            raise StateError("controller_parse_recovery_open")

    def _persist_lifecycle_evaluation(
        self, owner: dict, plan: dict, observations: dict | None = None
    ) -> dict:
        digest = plan["decision_sha256"]
        request_id = hashlib.sha256(
            f"{owner['owner']}\0{owner['epoch']}\0{digest}".encode()
        ).hexdigest()
        name = "lifecycle/request/" + request_id
        body = {"owner": owner["owner"], "epoch": owner["epoch"], "plan": plan}
        if observations is not None:
            body["observations"] = observations
        if len(_json(body).encode("utf-8")) > _LIFECYCLE_REQUEST_MAX_BYTES:
            raise StateError("lifecycle_request_byte_limit")
        versions = self._lifecycle_decision_versions(name)
        old = self.get_decision(name)
        if old is not None:
            if versions != [1] or old["version"] != 1:
                raise StateError("lifecycle_request_history_invalid")
            if _json(old["body"]) != _json(body):
                raise StateError("lifecycle_request_identity_conflict")
            return {"id": name, "version": old["version"], "body": old["body"]}
        if versions:
            raise StateError("lifecycle_request_history_invalid")
        version = self._put_decision_in_transaction(name, body)
        return {"id": name, "version": version, "body": body}

    def lifecycle_request_worklist(self, *, limit: int = 100) -> dict:
        """Return a bounded, read-only projection of immutable lifecycle requests.

        This exposes journal and current owner-lease state only. It grants no launch,
        dispatch, consumption, or disposition authority; caller-supplied gate observations
        remain claims rather than proof of current launch authorization.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StateError("lifecycle request worklist limit invalid")
        with self._read_snapshot():
            return self._lifecycle_request_worklist_in_snapshot(limit=limit)

    def _lifecycle_request_worklist_in_snapshot(self, *, limit: int) -> dict:
        request_names = self._lifecycle_request_names_in_snapshot(limit=limit)
        rows = [{"name": name} for name in request_names]
        owner_head = self._lifecycle_owner_history_head()
        owner_record = self.get_decision("lifecycle/owner")
        if (owner_record is None) != (owner_head is None) or (
            owner_record is not None and owner_record["version"] != owner_head
        ):
            raise StateError("lifecycle_decision_history_invalid")
        captured_at = self._lifecycle_now()
        owner = None
        if owner_record is not None:
            owner = self._lifecycle_fence_state(owner_record["body"])
            owner = {
                "owner": owner["owner"],
                "epoch": owner["epoch"],
                "status": owner["status"],
                "lease_until": owner["lease_until"],
                "live": owner["status"] == "active" and owner["lease_until"] > captured_at,
            }
        if len(rows) > limit:
            return {
                "items": [],
                "count": None,
                "limit": limit,
                "payload_bytes": None,
                "payload_bytes_limit": _LIFECYCLE_WORKLIST_MAX_BYTES,
                "overflow": True,
                "captured_at": captured_at,
                "owner": owner,
                "dispatch_authority": False,
                "consumption_authority": False,
            }

        request_sizes = {}
        payload_bytes = 0
        for row in rows:
            name = row["name"]
            if re.fullmatch(r"lifecycle/request/[0-9a-f]{64}", name) is None:
                raise StateError("lifecycle_request_identity_invalid")
            history = self.db.execute(
                "SELECT version, length(CAST(body AS BLOB)) AS body_bytes FROM decisions "
                "WHERE name=? ORDER BY version LIMIT 2",
                (name,),
            ).fetchall()
            if [item["version"] for item in history] != [1]:
                raise StateError("lifecycle_request_history_invalid")
            request_bytes = history[0]["body_bytes"]
            if type(request_bytes) is not int or request_bytes < 0:
                raise StateError("lifecycle_request_invalid")
            if request_bytes > _LIFECYCLE_REQUEST_MAX_BYTES:
                raise StateError("lifecycle_request_byte_limit")
            payload_bytes += request_bytes
            if payload_bytes > _LIFECYCLE_WORKLIST_MAX_BYTES:
                raise StateError("lifecycle_worklist_byte_limit")
            request_sizes[name] = request_bytes

        items = []
        for row in rows:
            name = row["name"]
            match = re.fullmatch(r"lifecycle/request/([0-9a-f]{64})", name)
            if match is None:
                raise StateError("lifecycle_request_identity_invalid")
            record = self.get_decision(name)
            if record is None or record["version"] != 1 or request_sizes.get(name) is None:
                raise StateError("lifecycle_request_history_invalid")
            body = record["body"]
            if type(body) is not dict or set(body) not in (
                {"owner", "epoch", "plan"},
                {"owner", "epoch", "plan", "observations"},
            ):
                raise StateError("lifecycle_request_invalid")
            request_owner, request_epoch, plan = body["owner"], body["epoch"], body["plan"]
            required_plan = {
                "action",
                "reason",
                "retry_at",
                "attempt_id",
                "request_owner",
                "request_epoch",
                "decision_sha256",
            }
            if (
                type(request_owner) is not str
                or not request_owner.strip()
                or len(request_owner) > 200
                or type(request_epoch) is not int
                or request_epoch <= 0
                or type(plan) is not dict
                or set(plan) != required_plan
                or type(plan["action"]) is not str
                or plan["action"]
                not in {
                    "hold",
                    "wait",
                    "observe",
                    "recovery_required",
                    "launch_request",
                }
                or type(plan["reason"]) is not str
                or not plan["reason"].strip()
                or len(plan["reason"]) > 500
            ):
                raise StateError("lifecycle_request_invalid")
            retry_at = plan["retry_at"]
            if retry_at is not None:
                if isinstance(retry_at, bool) or not isinstance(retry_at, (int, float)):
                    raise StateError("lifecycle_request_invalid")
                try:
                    finite_retry_at = math.isfinite(retry_at)
                except OverflowError:
                    finite_retry_at = False
                if not finite_retry_at:
                    raise StateError("lifecycle_request_invalid")
            attempt_id = plan["attempt_id"]
            if attempt_id is not None and (type(attempt_id) is not int or attempt_id <= 0):
                raise StateError("lifecycle_request_invalid")
            if plan["action"] == "launch_request":
                if (
                    plan["request_owner"] != request_owner
                    or plan["request_epoch"] != request_epoch
                    or type(plan["request_epoch"]) is not int
                    or plan["request_epoch"] <= 0
                    or "observations" not in body
                ):
                    raise StateError("lifecycle_request_invalid")
            elif plan["request_owner"] is not None or plan["request_epoch"] is not None:
                raise StateError("lifecycle_request_invalid")
            digest = plan["decision_sha256"]
            if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise StateError("lifecycle_request_invalid")
            expected_id = hashlib.sha256(
                f"{request_owner}\0{request_epoch}\0{digest}".encode()
            ).hexdigest()
            if expected_id != match.group(1):
                raise StateError("lifecycle_request_identity_invalid")
            if "observations" in body:
                observations = body["observations"]
                if type(observations) is not dict or set(observations) != (
                    _LIFECYCLE_OBSERVATION_FIELDS | {"request_owner", "request_epoch"}
                ):
                    raise StateError("lifecycle_request_invalid")
                if (
                    observations["request_owner"] != request_owner
                    or type(observations["request_epoch"]) is not int
                    or observations["request_epoch"] != request_epoch
                ):
                    raise StateError("lifecycle_request_invalid")
                planner_observations = {
                    key: value
                    for key, value in observations.items()
                    if key in _LIFECYCLE_OBSERVATION_FIELDS
                }
                try:
                    from .lifecycle import plan_lifecycle

                    computed = plan_lifecycle(
                        **planner_observations,
                        request_owner=request_owner,
                        request_epoch=request_epoch,
                    )
                except (TypeError, ValueError, OverflowError, RecursionError):
                    raise StateError("lifecycle_request_invalid") from None
                computed_plan = {
                    "action": computed.action,
                    "reason": computed.reason,
                    "retry_at": computed.retry_at,
                    "attempt_id": computed.attempt_id,
                    "request_owner": computed.request_owner,
                    "request_epoch": computed.request_epoch,
                    "decision_sha256": computed.decision_sha256,
                }
                if computed_plan != plan:
                    raise StateError("lifecycle_request_invalid")
            items.append(
                {
                    "request_id": match.group(1),
                    "decision_name": name,
                    "journal_version": record["version"],
                    "owner": request_owner,
                    "epoch": request_epoch,
                    "action": plan["action"],
                    "reason": plan["reason"],
                    "retry_at": plan["retry_at"],
                    "decision_sha256": digest,
                    "owner_current": bool(
                        owner
                        and owner["live"]
                        and owner["owner"] == request_owner
                        and owner["epoch"] == request_epoch
                    ),
                }
            )
        return {
            "items": items,
            "count": len(items),
            "limit": limit,
            "payload_bytes": payload_bytes,
            "payload_bytes_limit": _LIFECYCLE_WORKLIST_MAX_BYTES,
            "overflow": False,
            "captured_at": captured_at,
            "owner": owner,
            "dispatch_authority": False,
            "consumption_authority": False,
        }

    def _lifecycle_request_names_in_snapshot(self, *, limit: int) -> list[str]:
        """Seek a bounded number of distinct request journals by primary-key name."""
        names = []
        cursor = "lifecycle/request/"
        query = _LIFECYCLE_REQUEST_FIRST_QUERY
        while len(names) <= limit:
            row = self.db.execute(query, (cursor,)).fetchone()
            if row is None:
                break
            cursor = row["name"]
            names.append(cursor)
            query = _LIFECYCLE_REQUEST_NEXT_QUERY
        return names

    def open_controller_parse_fault(
        self,
        attempt_id: int,
        epoch: int,
        *,
        stage: str,
        exception_type: str,
    ) -> None:
        _name(stage), _name(exception_type)
        with self._tx():
            task, _attempt = self._fence(attempt_id, epoch)
            current = self.get_decision("circuit/controller-parse")
            if current is not None and current["body"].get("status") == "open":
                return
            circuit = {
                "status": "open",
                "fault": "structured_response",
                "first_attempt_id": attempt_id,
                "task_id": task["id"],
                "stage": stage,
                "exception_type": exception_type,
            }
            obligation = {
                "status": "open",
                "fault": "structured_response",
                "first_attempt_id": attempt_id,
                "task_id": task["id"],
                "stage": stage,
                "required_evidence": [
                    "qualified parser/controller fix",
                    "independent release review",
                    "separate bounded recovery authorization",
                ],
            }
            self._put_decision_in_transaction("circuit/controller-parse", circuit)
            self._put_decision_in_transaction("recovery-obligation/controller-parse", obligation)
            state = self._retry_state(task, False)
            self.db.execute("UPDATE attempts SET state='failed' WHERE id=?", (attempt_id,))
            self.db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task["id"]))
            self._event(
                "attempt_failed",
                task["id"],
                {
                    "attempt_id": attempt_id,
                    "epoch": epoch,
                    "reason": "controller_fault:structured_response",
                    "state": state,
                },
            )

    def resolve_controller_parse_fault(
        self,
        *,
        expected_circuit_version: int,
        expected_obligation_version: int,
        recovery_id: str,
        fix_sha256: str,
        review_ref: str,
        authorization_ref: str,
    ) -> None:
        with self._tx():
            circuit = self.get_decision("circuit/controller-parse")
            obligation = self.get_decision("recovery-obligation/controller-parse")
            if (
                circuit is None
                or obligation is None
                or circuit["version"] != expected_circuit_version
                or obligation["version"] != expected_obligation_version
                or circuit["body"].get("status") != "open"
                or obligation["body"].get("status") != "open"
            ):
                raise StateError("controller_recovery_obligation_changed")
            resolved = {
                "status": "resolved",
                "recovery_id": recovery_id,
                "fix_sha256": fix_sha256,
                "review_ref": review_ref,
                "authorization_ref": authorization_ref,
            }
            self._put_decision_in_transaction(
                "circuit/controller-parse", {**resolved, "fault": "structured_response"}
            )
            self._put_decision_in_transaction("recovery-obligation/controller-parse", resolved)

    def get_decision(self, name: str, version: int | None = None) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM decisions WHERE name=? AND (? IS NULL OR version=?) "
                "ORDER BY version DESC LIMIT 1",
                (name, version, version),
            ).fetchone()
            return None if row is None else {**dict(row), "body": json.loads(row["body"])}

    def record_usage(self, attempt_id: int, epoch: int, usage: dict) -> None:
        for key, value in usage.items():
            _name(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise StateError("usage values must be finite nonnegative numbers")
        with self._tx():
            task, _ = self._fence(attempt_id, epoch)
            self.db.execute(
                "INSERT INTO usage(attempt_id,epoch,body) VALUES(?,?,?)",
                (attempt_id, epoch, _json(usage)),
            )
            self._event(
                "usage_recorded",
                task["id"],
                {"attempt_id": attempt_id, "epoch": epoch, "usage": usage},
            )

    def usage_summary(self) -> dict:
        """Sum incremental measurements, including failed/expired executions."""
        with self._lock:
            total: dict = {}
            teams: dict = {}
            for row in self.db.execute(
                "SELECT u.body,t.team FROM usage u JOIN attempts a ON a.id=u.attempt_id "
                "JOIN tasks t ON t.id=a.task_id"
            ):
                team = teams.setdefault(row["team"], {})
                for key, value in json.loads(row["body"]).items():
                    total[key] = total.get(key, 0) + value
                    team[key] = team.get(key, 0) + value
            return {"total": total, "teams": teams}

    def list_attempts(self, task_id: str | None = None) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM attempts WHERE (? IS NULL OR task_id=?) ORDER BY id",
                (task_id, task_id),
            )
            return [
                {**dict(row), "session": json.loads(row["session"]) if row["session"] else None}
                for row in rows
            ]

    def events(self, after: int = 0) -> list[dict]:
        with self._lock:
            return [
                {**dict(row), "body": json.loads(row["body"])}
                for row in self.db.execute(
                    "SELECT * FROM events WHERE seq>? ORDER BY seq", (after,)
                )
            ]
