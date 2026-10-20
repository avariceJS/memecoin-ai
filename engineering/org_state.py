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


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


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

    def _lifecycle_fence(self, lease: Any) -> dict:
        current = self.get_decision("lifecycle/owner")
        versions = self._lifecycle_decision_versions("lifecycle/owner")
        if (current is None) != (not versions) or (
            current is not None and current["version"] != versions[-1]
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
            versions = self._lifecycle_decision_versions("lifecycle/owner")
            if (current is None) != (not versions) or (
                current is not None and current["version"] != versions[-1]
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
            return self._persist_lifecycle_evaluation(owner, plan, bound_observations)

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
