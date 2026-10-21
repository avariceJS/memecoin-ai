"""Episodic CEO governance on the engineering Store's durable journal.

This module records responsibility, decisions and requests. It grants no tools,
execution permissions, budget, policy changes, or scheduling authority. Pending
requests never change task readiness. All writes require the current CEO fence;
conversations are disposable and open obligations survive their replacement.
"""

from __future__ import annotations

import hashlib
import json
import math

from .org_state import StateError, Store, _json, _name, _positive

MODEL = "gpt-6-astra"
REASONING = "high"
DEPARTMENT_MODEL = "gpt-6.1-sol"
DEPARTMENT_REASONING = "high"
MAX_ACTIVE_DEPARTMENT_LEASES = 2
MAX_CONFLICT_ADJUDICATION_REVISIONS = 32


class ExecutiveError(StateError):
    """Malformed governance record or invalid transition."""


class StaleExecutive(ExecutiveError):
    """The executive episode has expired, been released, or been replaced."""


def _text(value: str, *, required=False, limit=4000) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ExecutiveError(
            "bounded nonempty text required" if required else "bounded text required"
        )
    return value


def _refs(values) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > 50:
        raise ExecutiveError("references must be a bounded list")
    return [_text(value, required=True, limit=500) for value in values]


class Executive:
    """Use Executive(Store(path)); caller owns and closes the shared Store.

    Lease dictionaries are fence tokens. Only role/epoch/owner identify a token;
    renewal does not invalidate the caller's older lease_until snapshot.
    Business IDs are immutable and exact duplicate submissions are idempotent.
    """

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        with store._tx():
            for sql in (
                "CREATE TABLE IF NOT EXISTS executive_role "
                "(role TEXT PRIMARY KEY, body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS executive_decisions "
                "(id TEXT PRIMARY KEY, body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS executive_obligations "
                "(id TEXT PRIMARY KEY, body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS executive_requests "
                "(id TEXT PRIMARY KEY, body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS executive_department_leases "
                "(team TEXT PRIMARY KEY, epoch INTEGER NOT NULL, lease_until REAL NOT NULL, "
                "status TEXT NOT NULL, body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS executive_department_plans "
                "(team TEXT NOT NULL, revision INTEGER NOT NULL, review_at REAL NOT NULL, "
                "body TEXT NOT NULL, "
                "PRIMARY KEY(team, revision))",
                "CREATE TABLE IF NOT EXISTS executive_department_conflicts "
                "(id TEXT PRIMARY KEY, team_a TEXT NOT NULL, team_a_revision INTEGER NOT NULL, "
                "team_b TEXT NOT NULL, team_b_revision INTEGER NOT NULL, "
                "supersedes_id TEXT UNIQUE REFERENCES executive_department_conflicts(id), "
                "body TEXT NOT NULL, CHECK(team_a < team_b))",
                "CREATE TABLE IF NOT EXISTS executive_conflict_adjudications "
                "(conflict_id TEXT NOT NULL REFERENCES executive_department_conflicts(id), "
                "revision INTEGER NOT NULL, disposition TEXT NOT NULL, body TEXT NOT NULL, "
                "PRIMARY KEY(conflict_id, revision))",
            ):
                self.db.execute(sql)

    def _role(self):
        row = self.db.execute("SELECT body FROM executive_role WHERE role='CEO'").fetchone()
        return json.loads(row[0]) if row else None

    def _journal(self, kind, entity, body, lease=None):
        actor = (
            {k: lease[k] for k in ("role", "epoch", "owner", "model", "reasoning")}
            if lease
            else None
        )
        self.store._event(kind, entity, {"actor": actor, **body})
        return self.db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _save_role(self, role):
        self.db.execute(
            "INSERT INTO executive_role VALUES('CEO',?) ON CONFLICT(role) "
            "DO UPDATE SET body=excluded.body",
            (_json(role),),
        )

    def claim(self, owner: str, *, model=MODEL, reasoning=REASONING, lease_seconds=300):
        _name(owner)
        _positive(lease_seconds)
        if model != MODEL or reasoning != REASONING:
            raise ExecutiveError("CEO requires gpt-6-astra with high reasoning")
        with self.store._tx():
            old = self._role()
            now = self.store.clock()
            if old and old["status"] == "active" and old["lease_until"] > now:
                return None
            if old and old["status"] == "active":
                self._journal("executive_expired", "CEO", {"expired_epoch": old["epoch"]}, old)
            role = {
                "role": "CEO",
                "epoch": old["epoch"] + 1 if old else 1,
                "owner": owner,
                "model": model,
                "reasoning": reasoning,
                "lease_until": now + lease_seconds,
                "status": "active",
            }
            role["last_event_seq"] = self._journal("executive_claimed", "CEO", {}, role)
            self._save_role(role)
            return role

    def _fence(self, lease):
        role = self._role()
        if (
            not isinstance(lease, dict)
            or not role
            or any(lease.get(key) != role[key] for key in ("role", "epoch", "owner"))
            or role["status"] != "active"
            or role["lease_until"] <= self.store.clock()
        ):
            raise StaleExecutive("executive lease is no longer current")
        return role

    @staticmethod
    def _executive_lease_snapshot(lease):
        try:
            detached = json.loads(_json({key: lease[key] for key in ("role", "epoch", "owner")}))
        except (TypeError, KeyError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StaleExecutive("executive lease is no longer current") from None
        if (
            type(detached) is not dict
            or detached["role"] != "CEO"
            or type(detached["epoch"]) is not int
            or detached["epoch"] <= 0
            or type(detached["owner"]) is not str
            or not detached["owner"].strip()
        ):
            raise StaleExecutive("executive lease is no longer current")
        return detached

    def renew(self, lease, lease_seconds=300):
        _positive(lease_seconds)
        with self.store._tx():
            role = self._fence(lease)
            role["lease_until"] = self.store.clock() + lease_seconds
            role["last_event_seq"] = self._journal("executive_renewed", "CEO", {}, role)
            self._save_role(role)
            return role

    def release(self, lease, summary=""):
        _text(summary, limit=1000)
        with self.store._tx():
            role = self._fence(lease)
            role.update(status="released", lease_until=self.store.clock())
            role["last_event_seq"] = self._journal(
                "executive_released", "CEO", {"summary": summary}, role
            )
            self._save_role(role)

    @staticmethod
    def _department_actor(team, lease):
        return {
            "role": "department:" + team,
            "epoch": lease["epoch"],
            "owner": lease["owner"],
            "model": lease["model"],
            "reasoning": lease["reasoning"],
        }

    def _department_lease(self, team):
        row = self.db.execute(
            "SELECT epoch,lease_until,status,body FROM executive_department_leases WHERE team=?",
            (team,),
        ).fetchone()
        if row is None:
            return None
        try:
            lease = json.loads(row[3])
        except (TypeError, ValueError, RecursionError):
            raise ExecutiveError("department lease record is malformed") from None
        expected = {"team", "epoch", "owner", "model", "reasoning", "lease_until", "status"}
        if (
            type(lease) is not dict
            or set(lease) != expected
            or lease["team"] != team
            or type(lease["team"]) is not str
            or type(lease["epoch"]) is not int
            or lease["epoch"] <= 0
            or lease["epoch"] != row[0]
            or type(lease["owner"]) is not str
            or not lease["owner"].strip()
            or lease["model"] != DEPARTMENT_MODEL
            or lease["reasoning"] != DEPARTMENT_REASONING
            or isinstance(lease["lease_until"], bool)
            or not isinstance(lease["lease_until"], (int, float))
            or not math.isfinite(lease["lease_until"])
            or lease["lease_until"] != row[1]
            or lease["status"] not in ("active", "released")
            or lease["status"] != row[2]
        ):
            raise ExecutiveError("department lease record is malformed")
        return lease

    @staticmethod
    def _department_plan_record(team, revision, review_at, body):
        try:
            plan = json.loads(body)
        except (TypeError, ValueError, RecursionError):
            raise ExecutiveError("department plan record is malformed") from None
        expected = {
            "team",
            "revision",
            "supersedes_revision",
            "objective_id",
            "summary",
            "priorities",
            "rationale",
            "evidence_refs",
            "blocked_by",
            "review_at",
            "owner",
            "owner_epoch",
            "created_seq",
        }
        supersedes_revision = revision - 1 if revision > 1 else None
        if (
            type(plan) is not dict
            or set(plan) != expected
            or plan["team"] != team
            or type(plan["team"]) is not str
            or type(plan["revision"]) is not int
            or plan["revision"] != revision
            or type(plan["supersedes_revision"]) is not type(supersedes_revision)
            or plan["supersedes_revision"] != supersedes_revision
            or type(plan["objective_id"]) is not str
            or type(plan["summary"]) is not str
            or type(plan["priorities"]) is not list
            or type(plan["rationale"]) is not str
            or type(plan["evidence_refs"]) is not list
            or type(plan["blocked_by"]) is not list
            or isinstance(plan["review_at"], bool)
            or not isinstance(plan["review_at"], (int, float))
            or not math.isfinite(plan["review_at"])
            or float(plan["review_at"]) != review_at
            or type(plan["owner"]) is not str
            or type(plan["owner_epoch"]) is not int
            or plan["owner_epoch"] <= 0
            or type(plan["created_seq"]) is not int
            or plan["created_seq"] <= 0
        ):
            raise ExecutiveError("department plan record is malformed")
        try:
            _text(plan["objective_id"], required=True, limit=200)
            _text(plan["summary"], required=True, limit=2000)
            _text(plan["rationale"], limit=2000)
            _text(plan["owner"], required=True, limit=200)
            if not 0 < len(plan["priorities"]) <= 20:
                raise ExecutiveError("department plan record is malformed")
            priorities = [_text(item, required=True, limit=300) for item in plan["priorities"]]
            if len(set(priorities)) != len(priorities):
                raise ExecutiveError("department plan record is malformed")
            if not _refs(plan["evidence_refs"]):
                raise ExecutiveError("department plan record is malformed")
            _refs(plan["blocked_by"])
        except ExecutiveError:
            raise ExecutiveError("department plan record is malformed") from None
        return plan

    @staticmethod
    def _department_lease_snapshot(lease):
        try:
            detached = json.loads(_json(lease))
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StaleExecutive("department lease is no longer current") from None
        expected = {"team", "epoch", "owner", "model", "reasoning", "lease_until", "status"}
        if (
            type(detached) is not dict
            or set(detached) != expected
            or type(detached["team"]) is not str
            or type(detached["epoch"]) is not int
            or detached["epoch"] <= 0
            or type(detached["owner"]) is not str
            or type(detached["model"]) is not str
            or type(detached["reasoning"]) is not str
            or isinstance(detached["lease_until"], bool)
            or not isinstance(detached["lease_until"], (int, float))
            or not math.isfinite(detached["lease_until"])
            or detached["status"] != "active"
        ):
            raise StaleExecutive("department lease is no longer current")
        return detached

    def get_department_lease(self, team):
        _name(team)
        with self.store._lock:
            lease = self._department_lease(team)
            if lease and lease["status"] == "active" and lease["lease_until"] <= self.store.clock():
                return {**lease, "status": "expired"}
            return lease

    def claim_department(
        self,
        team: str,
        owner: str,
        *,
        model=DEPARTMENT_MODEL,
        reasoning=DEPARTMENT_REASONING,
        lease_seconds=300,
    ):
        """Claim a bookkeeping lease for one department; it grants no execution authority."""
        _text(team, required=True, limit=200)
        _text(owner, required=True, limit=200)
        _positive(lease_seconds)
        if lease_seconds > 3600:
            raise ExecutiveError("department lease cannot exceed one hour")
        if model != DEPARTMENT_MODEL or reasoning != DEPARTMENT_REASONING:
            raise ExecutiveError("department lead requires gpt-6.1-sol with high reasoning")
        with self.store._tx():
            old = self._department_lease(team)
            now = self.store.clock()
            if old and old["status"] == "active" and old["lease_until"] > now:
                return None
            active_others = self.db.execute(
                "SELECT COUNT(*) FROM executive_department_leases "
                "WHERE team<>? AND status='active' AND lease_until>?",
                (team, now),
            ).fetchone()[0]
            if active_others >= MAX_ACTIVE_DEPARTMENT_LEASES:
                return None
            if old and old["status"] == "active":
                self.store._event(
                    "department_lease_expired",
                    team,
                    {
                        "actor": self._department_actor(team, old),
                        "expired_epoch": old["epoch"],
                    },
                )
            lease = {
                "team": team,
                "epoch": old["epoch"] + 1 if old else 1,
                "owner": owner,
                "model": model,
                "reasoning": reasoning,
                "lease_until": now + lease_seconds,
                "status": "active",
            }
            body = {"actor": self._department_actor(team, lease)}
            self.store._event("department_lease_claimed", team, body)
            self.db.execute(
                "INSERT INTO executive_department_leases VALUES(?,?,?,?,?) "
                "ON CONFLICT(team) DO UPDATE SET epoch=excluded.epoch, "
                "lease_until=excluded.lease_until, status=excluded.status, body=excluded.body",
                (team, lease["epoch"], lease["lease_until"], lease["status"], _json(lease)),
            )
            return lease

    def _department_fence(self, lease):
        if (
            not isinstance(lease, dict)
            or type(lease.get("team")) is not str
            or type(lease.get("epoch")) is not int
            or type(lease.get("owner")) is not str
            or type(lease.get("model")) is not str
            or type(lease.get("reasoning")) is not str
        ):
            raise StaleExecutive("department lease is no longer current")
        current = self._department_lease(lease["team"])
        now = self.store.clock()
        if (
            not current
            or any(
                lease.get(key) != current.get(key)
                for key in ("team", "epoch", "owner", "model", "reasoning")
            )
            or current["status"] != "active"
            or current["lease_until"] <= now
        ):
            raise StaleExecutive("department lease is no longer current")
        return current

    def renew_department(self, lease, lease_seconds=300):
        lease = self._department_lease_snapshot(lease)
        _positive(lease_seconds)
        if lease_seconds > 3600:
            raise ExecutiveError("department lease cannot exceed one hour")
        with self.store._tx():
            current = self._department_fence(lease)
            current["lease_until"] = self.store.clock() + lease_seconds
            self.store._event(
                "department_lease_renewed",
                current["team"],
                {"actor": self._department_actor(current["team"], current)},
            )
            self.db.execute(
                "UPDATE executive_department_leases SET epoch=?,lease_until=?,status=?,body=? "
                "WHERE team=?",
                (
                    current["epoch"],
                    current["lease_until"],
                    current["status"],
                    _json(current),
                    current["team"],
                ),
            )
            return current

    def release_department(self, lease, summary=""):
        lease = self._department_lease_snapshot(lease)
        _text(summary, limit=1000)
        with self.store._tx():
            current = self._department_fence(lease)
            current.update(status="released", lease_until=self.store.clock())
            self.store._event(
                "department_lease_released",
                current["team"],
                {"actor": self._department_actor(current["team"], current), "summary": summary},
            )
            self.db.execute(
                "UPDATE executive_department_leases SET epoch=?,lease_until=?,status=?,body=? "
                "WHERE team=?",
                (
                    current["epoch"],
                    current["lease_until"],
                    current["status"],
                    _json(current),
                    current["team"],
                ),
            )

    def record_department_plan(
        self,
        lease,
        revision: int,
        objective_id: str,
        summary: str,
        priorities,
        review_at: float,
        *,
        rationale="",
        evidence_refs=(),
        blocked_by=(),
    ):
        """Append one bounded, non-executable department plan under its current lease."""
        lease = self._department_lease_snapshot(lease)
        if type(revision) is not int or revision <= 0:
            raise ExecutiveError("department plan revision must be a positive integer")
        _text(objective_id, required=True, limit=200)
        _text(summary, required=True, limit=2000)
        _text(rationale, limit=2000)
        if not isinstance(priorities, (list, tuple)) or len(priorities) > 20:
            raise ExecutiveError("department priorities must be a bounded list")
        priorities = [_text(item, required=True, limit=300) for item in priorities]
        if not priorities or len(set(priorities)) != len(priorities):
            raise ExecutiveError("department priorities must be nonempty and unique")
        evidence = _refs(evidence_refs)
        if not evidence:
            raise ExecutiveError("department plan requires evidence references")
        blockers = _refs(blocked_by)
        if (
            isinstance(review_at, bool)
            or not isinstance(review_at, (int, float))
            or not math.isfinite(review_at)
        ):
            raise ExecutiveError("department review time must be finite")
        review_at = float(review_at)
        team = lease.get("team") if isinstance(lease, dict) else None
        if not isinstance(team, str):
            raise StaleExecutive("department lease is no longer current")
        plan = {
            "team": team,
            "revision": revision,
            "supersedes_revision": revision - 1 if revision > 1 else None,
            "objective_id": objective_id,
            "summary": summary,
            "priorities": priorities,
            "rationale": rationale,
            "evidence_refs": evidence,
            "blocked_by": blockers,
            "review_at": review_at,
        }
        if len(_json(plan).encode("utf-8")) > 65_536:
            raise ExecutiveError("department plan exceeds byte limit")
        with self.store._tx():
            current = self._department_fence(lease)
            if not self.db.execute(
                "SELECT 1 FROM objectives WHERE id=?", (objective_id,)
            ).fetchone():
                raise ExecutiveError("department objective does not exist")
            latest = self.db.execute(
                "SELECT revision,review_at,body FROM executive_department_plans "
                "WHERE team=? ORDER BY revision DESC LIMIT 1",
                (team,),
            ).fetchone()
            if latest is not None and latest[0] == revision:
                previous = self._department_plan_record(team, latest[0], latest[1], latest[2])
                if all(previous.get(key) == value for key, value in plan.items()):
                    state = "due" if review_at <= self.store.clock() else "scheduled"
                    return {**previous, "status": "current", "review_state": state}
                raise ExecutiveError("department plan revision is immutable")
            expected_revision = latest[0] + 1 if latest is not None else 1
            if revision != expected_revision:
                raise ExecutiveError("department plan revision is not the next revision")
            if review_at <= self.store.clock():
                raise ExecutiveError("department review time must be in the future")
            record = {
                **plan,
                "owner": current["owner"],
                "owner_epoch": current["epoch"],
            }
            self.store._event(
                "department_plan_recorded",
                f"{team}/{revision}",
                {"actor": self._department_actor(team, current), "plan": record},
            )
            event = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            record["created_seq"] = event
            self.db.execute(
                "INSERT INTO executive_department_plans(team,revision,review_at,body) "
                "VALUES(?,?,?,?)",
                (team, revision, review_at, _json(record)),
            )
            return {**record, "status": "current", "review_state": "scheduled"}

    def get_department_plan(self, team: str, revision=None):
        _name(team)
        if revision is not None and (type(revision) is not int or revision <= 0):
            raise ExecutiveError("department plan revision must be a positive integer")
        with self.store._read_snapshot():
            if revision is None:
                row = self.db.execute(
                    "SELECT revision,review_at,body FROM executive_department_plans WHERE team=? "
                    "ORDER BY revision DESC LIMIT 1",
                    (team,),
                ).fetchone()
            else:
                row = self.db.execute(
                    "SELECT revision,review_at,body FROM executive_department_plans "
                    "WHERE team=? AND revision=?",
                    (team, revision),
                ).fetchone()
            if row is None:
                return None
            record = self._department_plan_record(team, row[0], row[1], row[2])
            latest = self.db.execute(
                "SELECT MAX(revision) FROM executive_department_plans WHERE team=?", (team,)
            ).fetchone()[0]
            record["status"] = "current" if record["revision"] == latest else "superseded"
            record["review_state"] = (
                "due" if record["review_at"] <= self.store.clock() else "scheduled"
            )
            return record

    @staticmethod
    def _department_conflict_record(body):
        try:
            conflict = json.loads(body)
        except (TypeError, ValueError, RecursionError):
            raise ExecutiveError("department conflict record is malformed") from None
        expected = {
            "id",
            "team_a",
            "team_a_revision",
            "team_b",
            "team_b_revision",
            "summary",
            "rationale",
            "evidence_refs",
            "raised_by",
            "raised_epoch",
            "raised_model",
            "supersedes_id",
            "created_seq",
        }
        if (
            type(conflict) is not dict
            or set(conflict) != expected
            or type(conflict["id"]) is not str
            or type(conflict["team_a"]) is not str
            or type(conflict["team_b"]) is not str
            or conflict["team_a"] >= conflict["team_b"]
            or type(conflict["team_a_revision"]) is not int
            or conflict["team_a_revision"] <= 0
            or type(conflict["team_b_revision"]) is not int
            or conflict["team_b_revision"] <= 0
            or type(conflict["summary"]) is not str
            or type(conflict["rationale"]) is not str
            or type(conflict["evidence_refs"]) is not list
            or type(conflict["raised_by"]) is not str
            or type(conflict["raised_epoch"]) is not int
            or conflict["raised_epoch"] <= 0
            or conflict["raised_model"] != DEPARTMENT_MODEL
            or (
                conflict["supersedes_id"] is not None and type(conflict["supersedes_id"]) is not str
            )
            or type(conflict["created_seq"]) is not int
            or conflict["created_seq"] <= 0
        ):
            raise ExecutiveError("department conflict record is malformed")
        try:
            _text(conflict["id"], required=True, limit=200)
            _text(conflict["team_a"], required=True, limit=200)
            _text(conflict["team_b"], required=True, limit=200)
            _text(conflict["summary"], required=True, limit=2000)
            _text(conflict["rationale"], limit=2000)
            _text(conflict["raised_by"], required=True, limit=200)
            if conflict["supersedes_id"] is not None:
                _text(conflict["supersedes_id"], required=True, limit=200)
            if not _refs(conflict["evidence_refs"]):
                raise ExecutiveError("department conflict record is malformed")
        except ExecutiveError:
            raise ExecutiveError("department conflict record is malformed") from None
        return conflict

    @staticmethod
    def _conflict_adjudication_record(body):
        try:
            adjudication = json.loads(body)
        except (TypeError, ValueError, RecursionError):
            raise ExecutiveError("conflict adjudication record is malformed") from None
        expected = {
            "conflict_id",
            "revision",
            "supersedes_revision",
            "disposition",
            "rationale",
            "evidence_refs",
            "conflict_sha256",
            "adjudicator",
            "adjudicator_epoch",
            "adjudicator_model",
            "created_seq",
        }
        supersedes = (
            adjudication.get("revision", 0) - 1
            if type(adjudication) is dict and type(adjudication.get("revision")) is int
            else -1
        )
        if (
            type(adjudication) is not dict
            or set(adjudication) != expected
            or type(adjudication["conflict_id"]) is not str
            or type(adjudication["revision"]) is not int
            or adjudication["revision"] <= 0
            or type(adjudication["supersedes_revision"])
            is not type(None if supersedes == 0 else supersedes)
            or adjudication["supersedes_revision"] != (None if supersedes == 0 else supersedes)
            or adjudication["disposition"]
            not in (
                "select_team_a",
                "select_team_b",
                "request_replan",
                "defer",
            )
            or type(adjudication["rationale"]) is not str
            or type(adjudication["evidence_refs"]) is not list
            or type(adjudication["conflict_sha256"]) is not str
            or len(adjudication["conflict_sha256"]) != 64
            or type(adjudication["adjudicator"]) is not str
            or type(adjudication["adjudicator_epoch"]) is not int
            or adjudication["adjudicator_epoch"] <= 0
            or adjudication["adjudicator_model"] != MODEL
            or type(adjudication["created_seq"]) is not int
            or adjudication["created_seq"] <= 0
        ):
            raise ExecutiveError("conflict adjudication record is malformed")
        try:
            _text(adjudication["conflict_id"], required=True, limit=200)
            _text(adjudication["rationale"], required=True, limit=2000)
            _text(adjudication["adjudicator"], required=True, limit=200)
            _refs(adjudication["evidence_refs"])
            if any(c not in "0123456789abcdef" for c in adjudication["conflict_sha256"]):
                raise ExecutiveError("conflict adjudication record is malformed")
        except ExecutiveError:
            raise ExecutiveError("conflict adjudication record is malformed") from None
        return adjudication

    def _department_conflict_adjudication_history(self, conflict):
        conflict_hash = hashlib.sha256(_json(conflict).encode("utf-8")).hexdigest()
        rows = self.db.execute(
            "SELECT revision,disposition,body FROM executive_conflict_adjudications "
            "WHERE conflict_id=? ORDER BY revision LIMIT ?",
            (conflict["id"], MAX_CONFLICT_ADJUDICATION_REVISIONS + 1),
        ).fetchall()
        if len(rows) > MAX_CONFLICT_ADJUDICATION_REVISIONS:
            raise ExecutiveError("conflict adjudication history exceeds revision limit")
        history = []
        for expected_revision, row in enumerate(rows, start=1):
            record = self._conflict_adjudication_record(row[2])
            if (
                row[0] != expected_revision
                or row[1] != record["disposition"]
                or record["conflict_id"] != conflict["id"]
                or record["revision"] != expected_revision
                or record["conflict_sha256"] != conflict_hash
            ):
                raise ExecutiveError("conflict adjudication history is malformed")
            history.append(record)
        return history

    def _current_department_plan_revision(self, team):
        row = self.db.execute(
            "SELECT revision,review_at,body FROM executive_department_plans "
            "WHERE team=? ORDER BY revision DESC LIMIT 1",
            (team,),
        ).fetchone()
        if row is None:
            return None
        self._department_plan_record(team, row[0], row[1], row[2])
        return row[0]

    def escalate_department_conflict(
        self,
        lease,
        conflict_id: str,
        other_team: str,
        summary: str,
        *,
        own_plan_revision: int,
        other_plan_revision: int,
        rationale="",
        evidence_refs=(),
        supersedes=None,
    ):
        """Record a conflict tied to two exact current plan revisions; no work is admitted."""
        lease = self._department_lease_snapshot(lease)
        _text(conflict_id, required=True, limit=200)
        _text(other_team, required=True, limit=200)
        _text(summary, required=True, limit=2000)
        _text(rationale, limit=2000)
        evidence = _refs(evidence_refs)
        if not evidence:
            raise ExecutiveError("department conflict requires evidence references")
        if other_team == lease["team"]:
            raise ExecutiveError("department conflict requires two distinct teams")
        if any(
            type(value) is not int or value <= 0
            for value in (own_plan_revision, other_plan_revision)
        ):
            raise ExecutiveError("department conflict plan revisions must be positive integers")
        if supersedes is not None:
            _text(supersedes, required=True, limit=200)
        team_revisions = {
            lease["team"]: own_plan_revision,
            other_team: other_plan_revision,
        }
        team_a, team_b = sorted(team_revisions)
        intent = {
            "id": conflict_id,
            "team_a": team_a,
            "team_a_revision": team_revisions[team_a],
            "team_b": team_b,
            "team_b_revision": team_revisions[team_b],
            "summary": summary,
            "rationale": rationale,
            "evidence_refs": evidence,
            "raised_by": lease["owner"],
            "raised_epoch": lease["epoch"],
            "raised_model": lease["model"],
            "supersedes_id": supersedes,
        }
        if len(_json(intent).encode("utf-8")) > 16_384:
            raise ExecutiveError("department conflict exceeds byte limit")
        with self.store._tx():
            self._department_fence(lease)
            existing = self.db.execute(
                "SELECT body FROM executive_department_conflicts WHERE id=?", (conflict_id,)
            ).fetchone()
            if existing is not None:
                previous = self._department_conflict_record(existing[0])
                if all(previous.get(key) == value for key, value in intent.items()):
                    return self._department_conflict_view_in_snapshot(conflict_id)
                raise ExecutiveError("department conflict ID reused with different content")
            for team, revision in team_revisions.items():
                if self._current_department_plan_revision(team) != revision:
                    raise ExecutiveError("department conflict plan revision is stale")
            if supersedes is not None:
                prior_conflict = self._department_conflict_view_in_snapshot(supersedes)
                if prior_conflict is None:
                    raise ExecutiveError("superseded department conflict does not exist")
                if (prior_conflict["team_a"], prior_conflict["team_b"]) != (team_a, team_b):
                    raise ExecutiveError("superseding conflict participants do not match")
                if (
                    prior_conflict["team_a_revision"] == team_revisions[team_a]
                    and prior_conflict["team_b_revision"] == team_revisions[team_b]
                ):
                    raise ExecutiveError("superseding conflict requires changed plan revisions")
                if self.db.execute(
                    "SELECT 1 FROM executive_department_conflicts WHERE supersedes_id=?",
                    (supersedes,),
                ).fetchone():
                    raise ExecutiveError("department conflict already has a successor")
            self.store._event(
                "department_conflict_escalated",
                conflict_id,
                {"actor": self._department_actor(lease["team"], lease), "conflict": intent},
            )
            created_seq = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            record = {**intent, "created_seq": created_seq}
            self.db.execute(
                "INSERT INTO executive_department_conflicts "
                "(id,team_a,team_a_revision,team_b,team_b_revision,supersedes_id,body) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    conflict_id,
                    team_a,
                    team_revisions[team_a],
                    team_b,
                    team_revisions[team_b],
                    supersedes,
                    _json(record),
                ),
            )
            return self._department_conflict_view_in_snapshot(conflict_id)

    def adjudicate_department_conflict(
        self,
        lease,
        conflict_id: str,
        revision: int,
        disposition: str,
        rationale: str,
        evidence_refs,
        *,
        supersedes_revision=None,
    ):
        """Append a CEO-fenced disposition without mutating plans or task readiness."""
        _text(conflict_id, required=True, limit=200)
        if type(revision) is not int or revision <= 0:
            raise ExecutiveError("conflict adjudication revision must be positive")
        if disposition not in ("select_team_a", "select_team_b", "request_replan", "defer"):
            raise ExecutiveError("unknown conflict disposition")
        rationale = _text(rationale, required=True, limit=2000)
        evidence = _refs(evidence_refs)
        if not evidence:
            raise ExecutiveError("conflict adjudication requires evidence references")
        if supersedes_revision is not None and (
            type(supersedes_revision) is not int or supersedes_revision <= 0
        ):
            raise ExecutiveError("superseded adjudication revision must be positive")
        lease = self._executive_lease_snapshot(lease)
        with self.store._tx():
            role = self._fence(lease)
            conflict_row = self.db.execute(
                "SELECT team_a,team_a_revision,team_b,team_b_revision,supersedes_id,body "
                "FROM executive_department_conflicts WHERE id=?",
                (conflict_id,),
            ).fetchone()
            if conflict_row is None:
                raise ExecutiveError("unknown department conflict")
            conflict = self._department_conflict_record(conflict_row[5])
            table_identity = tuple(conflict_row[index] for index in range(5))
            body_identity = (
                conflict["team_a"],
                conflict["team_a_revision"],
                conflict["team_b"],
                conflict["team_b_revision"],
                conflict["supersedes_id"],
            )
            if table_identity != body_identity:
                raise ExecutiveError("department conflict record is malformed")
            conflict_hash = hashlib.sha256(_json(conflict).encode("utf-8")).hexdigest()
            history = self._department_conflict_adjudication_history(conflict)
            intent = {
                "conflict_id": conflict_id,
                "revision": revision,
                "supersedes_revision": supersedes_revision,
                "disposition": disposition,
                "rationale": rationale,
                "evidence_refs": evidence,
                "conflict_sha256": conflict_hash,
            }
            if revision <= len(history):
                previous = history[revision - 1]
                if all(previous.get(key) == value for key, value in intent.items()):
                    return previous
                raise ExecutiveError("conflict adjudication revision is immutable")
            if self.db.execute(
                "SELECT 1 FROM executive_department_conflicts WHERE supersedes_id=?",
                (conflict_id,),
            ).fetchone():
                raise ExecutiveError("superseded department conflicts cannot be adjudicated")
            if (
                self._current_department_plan_revision(conflict["team_a"])
                != conflict["team_a_revision"]
                or self._current_department_plan_revision(conflict["team_b"])
                != conflict["team_b_revision"]
            ):
                raise ExecutiveError("department conflict plan revisions are stale")
            latest = history[-1]["revision"] if history else None
            expected_revision = latest + 1 if latest is not None else 1
            expected_supersedes = latest
            if revision != expected_revision or supersedes_revision != expected_supersedes:
                raise ExecutiveError("conflict adjudication revision is not the next revision")
            if revision > MAX_CONFLICT_ADJUDICATION_REVISIONS:
                raise ExecutiveError("conflict adjudication history reached its revision limit")
            if len(_json(intent).encode("utf-8")) > 16_384:
                raise ExecutiveError("conflict adjudication exceeds byte limit")
            record = {
                **intent,
                "adjudicator": role["owner"],
                "adjudicator_epoch": role["epoch"],
                "adjudicator_model": role["model"],
            }
            self.store._event(
                "department_conflict_adjudicated",
                f"{conflict_id}/{revision}",
                {"actor": {k: role[k] for k in ("role", "epoch", "owner")}, "adjudication": record},
            )
            record["created_seq"] = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.execute(
                "INSERT INTO executive_conflict_adjudications VALUES(?,?,?,?)",
                (conflict_id, revision, disposition, _json(record)),
            )
            role["last_event_seq"] = record["created_seq"]
            self._save_role(role)
            return record

    def _department_conflict_view_in_snapshot(self, conflict_id):
        row = self.db.execute(
            "SELECT team_a,team_a_revision,team_b,team_b_revision,supersedes_id,body "
            "FROM executive_department_conflicts WHERE id=?",
            (conflict_id,),
        ).fetchone()
        if row is None:
            return None
        conflict = self._department_conflict_record(row[5])
        if (row[0], row[1], row[2], row[3], row[4]) != (
            conflict["team_a"],
            conflict["team_a_revision"],
            conflict["team_b"],
            conflict["team_b_revision"],
            conflict["supersedes_id"],
        ):
            raise ExecutiveError("department conflict record is malformed")
        history = self._department_conflict_adjudication_history(conflict)
        adjudication = history[-1] if history else None
        successor = self.db.execute(
            "SELECT id FROM executive_department_conflicts WHERE supersedes_id=?", (conflict_id,)
        ).fetchone()
        source_stale = (
            self._current_department_plan_revision(conflict["team_a"])
            != conflict["team_a_revision"]
            or self._current_department_plan_revision(conflict["team_b"])
            != conflict["team_b_revision"]
        )
        if successor:
            status = "superseded"
        elif source_stale:
            status = "stale"
        elif adjudication and adjudication["disposition"] in ("select_team_a", "select_team_b"):
            status = "resolved"
        else:
            status = "open"
        return {**conflict, "status": status, "latest_adjudication": adjudication}

    def get_department_conflict(self, conflict_id: str):
        _text(conflict_id, required=True, limit=200)
        with self.store._read_snapshot():
            return self._department_conflict_view_in_snapshot(conflict_id)

    def _get(self, table, record_id):
        row = self.db.execute(
            f"SELECT body FROM executive_{table} WHERE id=?", (record_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_decision(self, decision_id):
        with self.store._lock:
            return self._get("decisions", decision_id)

    def get_obligation(self, obligation_id):
        with self.store._lock:
            return self._get("obligations", obligation_id)

    def get_request(self, request_id):
        with self.store._lock:
            return self._get("requests", request_id)

    def _create(self, lease, table, record_id, body):
        _name(record_id)
        with self.store._tx():
            role = self._fence(lease)
            return self._create_in_transaction(role, table, record_id, body)

    def _create_in_transaction(self, role, table, record_id, body):
        old = self._get(table, record_id)
        if old is not None:
            if old["initial"] != body:
                raise ExecutiveError("immutable governance ID reused with different content")
            return old
        seq = self._journal("executive_" + table + "_created", record_id, body, role)
        record = {
            "id": record_id,
            **body,
            "initial": body,
            "created_seq": seq,
            "updated_seq": seq,
            "created_epoch": role["epoch"],
            "created_owner": role["owner"],
        }
        self.db.execute(f"INSERT INTO executive_{table} VALUES(?,?)", (record_id, _json(record)))
        role["last_event_seq"] = seq
        self._save_role(role)
        return record

    def decision(
        self,
        lease,
        decision_id: str,
        summary: str,
        *,
        rationale="",
        evidence_refs=(),
        supersedes=None,
    ):
        body = {
            "summary": _text(summary, required=True, limit=1000),
            "rationale": _text(rationale),
            "evidence_refs": _refs(evidence_refs),
            "supersedes": supersedes,
        }
        if supersedes is not None:
            _name(supersedes)
        # Validation and append occur in one transaction in _create. Existing decisions
        # are immutable, so checking the reference before that transaction is safe.
        with self.store._lock:
            if supersedes is not None and self._get("decisions", supersedes) is None:
                raise ExecutiveError("superseded decision does not exist")
        return self._create(lease, "decisions", decision_id, body)

    def obligation(
        self,
        lease,
        obligation_id: str,
        title: str,
        *,
        objective_id=None,
        owner_role="CEO",
        team=None,
        rationale="",
        evidence_refs=(),
        blocked_by=(),
        details=None,
    ):
        if objective_id is not None:
            _name(objective_id)
        if team is not None:
            _name(team)
        body = {
            "title": _text(title, required=True, limit=1000),
            "objective_id": objective_id,
            "owner_role": _text(owner_role, required=True, limit=100),
            "team": team,
            "rationale": _text(rationale),
            "evidence_refs": _refs(evidence_refs),
            "blocked_by": _refs(blocked_by),
            "status": "blocked" if blocked_by else "open",
            "completion_evidence": [],
        }
        if details is not None:
            try:
                details_json = _json(details)
                detached_details = json.loads(details_json)
            except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
                raise ExecutiveError("invalid obligation details") from None
            if len(details_json.encode("utf-8")) > 65_536:
                raise ExecutiveError("obligation details exceed byte limit")
            body["details"] = detached_details
        return self._create(lease, "obligations", obligation_id, body)

    def transition_obligation(
        self,
        lease,
        obligation_id: str,
        status: str,
        *,
        completion_evidence=(),
        cancellation_evidence=(),
        blocked_by=(),
    ):
        if status not in ("open", "blocked", "completed", "cancelled"):
            raise ExecutiveError("unknown obligation status")
        evidence, blockers = _refs(completion_evidence), _refs(blocked_by)
        cancellation = _refs(cancellation_evidence)
        if status == "completed" and not evidence:
            raise ExecutiveError("completion requires evidence references")
        if status == "cancelled" and not cancellation:
            raise ExecutiveError("cancellation requires evidence references")
        if status == "blocked" and not blockers:
            raise ExecutiveError("blocked obligation requires blockers")
        if status != "blocked" and blockers:
            raise ExecutiveError("only blocked obligations may retain blockers")
        if status != "cancelled" and cancellation:
            raise ExecutiveError("only cancelled obligations may retain cancellation evidence")
        return self._transition(
            lease,
            "obligations",
            obligation_id,
            status,
            {
                "completion_evidence": evidence,
                "cancellation_evidence": cancellation,
                "blocked_by": blockers,
            },
            terminal=("completed", "cancelled"),
        )

    def reconcile_dependency_obligations(self, lease):
        """Atomically align durable dependency duties to one current readiness snapshot."""
        with self.store._tx():
            role = self._fence(lease)
            report = self.store._readiness_report_in_snapshot()
            blocked = [
                {"task_id": row["task_id"], "blockers": row["blockers"]}
                for row in report
                if row["state"] == "pending" and row["blockers"]
            ]
            ready = [
                row["task_id"] for row in report if row["state"] == "pending" and row["eligible"]
            ]
            snapshot = {"schema": "dependency-readiness-v1", "blocked": blocked, "ready": ready}
            snapshot_json = _json(snapshot)
            if len(snapshot_json.encode("utf-8")) > 65_536:
                raise ExecutiveError("dependency readiness snapshot exceeds byte limit")
            snapshot_sha256 = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            snapshot_ref = "dependency-readiness:" + snapshot_sha256

            rows = [
                json.loads(row[0])
                for row in self.db.execute("SELECT body FROM executive_obligations")
            ]
            if any(
                type(row) is not dict
                or type(row.get("id")) is not str
                or type(row.get("status")) is not str
                for row in rows
            ):
                raise ExecutiveError("dependency obligation history is malformed")
            for row in rows:
                if row["id"].startswith("blocked-dependencies/") and row["status"] not in (
                    "open",
                    "blocked",
                    "completed",
                    "cancelled",
                ):
                    raise ExecutiveError("dependency obligation status is malformed")
            active = [
                row
                for row in rows
                if row["id"].startswith("blocked-dependencies/")
                and row["status"] in ("open", "blocked")
            ]
            dependency_rows = [row for row in rows if row["id"].startswith("blocked-dependencies/")]
            for row in dependency_rows:
                details = row.get("details")
                if details is not None and type(details) is not dict:
                    raise ExecutiveError("dependency obligation details are malformed")
            matching = (
                [
                    row
                    for row in active
                    if type(row.get("details")) is dict
                    and row["details"].get("snapshot_sha256") == snapshot_sha256
                    and row["details"].get("snapshot") == snapshot
                ]
                if blocked
                else []
            )
            if len(matching) > 1:
                raise ExecutiveError("duplicate active dependency obligations")

            kept_id = matching[0]["id"] if matching else None
            stale = [row for row in active if row["id"] != kept_id]
            if stale:
                # Persist the content-addressed cancellation basis before the transitions
                # that cite it. Both the evidence event and cancellations share this tx.
                self._journal(
                    "dependency_readiness_reconciled",
                    snapshot_ref,
                    {"snapshot_sha256": snapshot_sha256, "snapshot": snapshot},
                    role,
                )
            for row in stale:
                self._transition_in_transaction(
                    role,
                    "obligations",
                    row["id"],
                    "cancelled",
                    {
                        "completion_evidence": row.get("completion_evidence", []),
                        "blocked_by": [],
                        "cancellation_evidence": [snapshot_ref],
                    },
                    terminal=("completed", "cancelled"),
                )

            obligation_id = kept_id
            if blocked and obligation_id is None:
                prefix = f"blocked-dependencies/{snapshot_sha256}/"
                generation = 1 + sum(row["id"].startswith(prefix) for row in rows)
                obligation_id = prefix + str(generation)
                blocked_by = [snapshot_ref]
                for item in blocked[:49]:
                    task_ref = "task:" + item["task_id"]
                    if len(task_ref) > 500:
                        task_ref = (
                            "task-sha256:"
                            + hashlib.sha256(item["task_id"].encode("utf-8")).hexdigest()
                        )
                    blocked_by.append(task_ref)
                body = {
                    "title": "Resolve blocked dependencies without suppressing ready work.",
                    "objective_id": None,
                    "owner_role": "CEO",
                    "team": None,
                    "rationale": (
                        f"{len(blocked)} pending tasks have unmet dependency or contract gates; "
                        f"{len(ready)} unrelated pending tasks remain ready."
                    ),
                    "evidence_refs": [snapshot_ref],
                    "blocked_by": blocked_by,
                    "status": "blocked",
                    "completion_evidence": [],
                    "details": {"snapshot_sha256": snapshot_sha256, "snapshot": snapshot},
                }
                if len(_json(body).encode("utf-8")) > 65_536:
                    raise ExecutiveError("dependency obligation exceeds byte limit")
                self._create_in_transaction(role, "obligations", obligation_id, body)
            return {
                "snapshot_sha256": snapshot_sha256,
                "blocked_task_count": len(blocked),
                "ready_task_count": len(ready),
                "obligation_id": obligation_id,
            }

    def request(
        self,
        lease,
        request_id: str,
        request_type: str,
        requester: str,
        requested_change: str,
        reason: str,
        alternative: str,
        *,
        evidence_refs=(),
    ):
        if request_type not in ("resource", "authority", "decision"):
            raise ExecutiveError("unknown request type")
        body = {
            "type": request_type,
            "requester": _text(requester, required=True, limit=200),
            "requested_change": _text(requested_change, required=True, limit=2000),
            "reason": _text(reason, required=True),
            "alternative": _text(alternative, required=True),
            "evidence_refs": _refs(evidence_refs),
            "status": "open",
            "resolution": "",
        }
        return self._create(lease, "requests", request_id, body)

    def resolve_request(
        self, lease, request_id: str, status: str, resolution: str, *, evidence_refs=()
    ):
        """Record a disposition; even 'resolved' grants no authority or resources."""
        if status not in ("resolved", "declined"):
            raise ExecutiveError("request resolution must be resolved or declined")
        return self._transition(
            lease,
            "requests",
            request_id,
            status,
            {
                "resolution": _text(resolution, required=True),
                "resolution_evidence": _refs(evidence_refs),
            },
            terminal=("resolved", "declined"),
        )

    def _transition(self, lease, table, record_id, status, changes, *, terminal):
        with self.store._tx():
            role = self._fence(lease)
            return self._transition_in_transaction(
                role, table, record_id, status, changes, terminal
            )

    def _transition_in_transaction(self, role, table, record_id, status, changes, terminal):
        old = self._get(table, record_id)
        if old is None:
            raise ExecutiveError("unknown governance record")
        if old["status"] == status and all(old.get(k) == v for k, v in changes.items()):
            return old
        if old["status"] in terminal:
            raise ExecutiveError("terminal records cannot reopen; create a new ID")
        seq = self._journal(
            "executive_" + table + "_transition",
            record_id,
            {"from": old["status"], "to": status, **changes},
            role,
        )
        record = {**old, **changes, "status": status, "updated_seq": seq}
        self.db.execute(
            f"UPDATE executive_{table} SET body=? WHERE id=?", (_json(record), record_id)
        )
        role["last_event_seq"] = seq
        self._save_role(role)
        return record

    def snapshot(self, *, history_limit=20, open_limit=20):
        """Bounded dossier shared by the episodic CEO and Founder Desk."""
        if type(history_limit) is not int or not 0 <= history_limit <= 20:
            raise ExecutiveError("history_limit must be between zero and twenty")
        if type(open_limit) is not int or not 0 <= open_limit <= 100:
            raise ExecutiveError("open_limit must be between zero and one hundred")
        with self.store._tx():
            role = self._role()
            now = self.store.clock()
            if role and role["status"] == "active" and role["lease_until"] <= now:
                role["status"] = "expired"
            rows = {}
            counts = {}
            for table in ("decisions", "obligations", "requests"):
                all_rows = [
                    json.loads(row[0])
                    for row in self.db.execute(
                        f"SELECT body FROM executive_{table} ORDER BY rowid DESC"
                    )
                ]
                if table == "obligations":
                    all_rows = [row for row in all_rows if row["status"] in ("open", "blocked")]
                elif table == "requests":
                    all_rows = [row for row in all_rows if row["status"] == "open"]
                counts[table] = len(all_rows)
                rows[table] = [
                    {k: v for k, v in row.items() if k != "initial"}
                    for row in all_rows[: history_limit if table == "decisions" else open_limit]
                ]
            contract_review_projection = (
                self.store._contract_transition_review_projection_in_snapshot(
                    limit=min(open_limit, 100)
                )
            )
            events = [
                dict(row)
                for row in self.db.execute(
                    "SELECT seq,at,kind,entity,body FROM events ORDER BY seq DESC LIMIT ?",
                    (history_limit,),
                )
            ]
            # Keep the main view concise; detailed immutable event payloads remain in Store.events.
            for event in events:
                body = json.loads(event.pop("body"))
                event["actor"] = body.get("actor")
            task_projection = self.store.active_task_projection(limit=100)
            objectives = [
                dict(row)
                for row in self.db.execute("SELECT * FROM objectives ORDER BY id LIMIT 20")
            ]
            accepted = [
                dict(row)
                for row in self.db.execute(
                    "SELECT task_id,sha,attempt_id,epoch FROM acceptances "
                    "ORDER BY rowid DESC LIMIT 20"
                )
            ]
            department_plan_rows = self.db.execute(
                "SELECT p.team,p.revision,p.review_at,p.body "
                "FROM executive_department_plans p "
                "JOIN (SELECT team,MAX(revision) AS revision "
                "FROM executive_department_plans GROUP BY team) latest "
                "ON latest.team=p.team AND latest.revision=p.revision "
                "ORDER BY p.team LIMIT ?",
                (open_limit,),
            ).fetchall()
            department_plan_count, department_plan_review_due_count = self.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN p.review_at <= ? "
                "THEN 1 ELSE 0 END),0) FROM executive_department_plans p "
                "WHERE p.revision=(SELECT MAX(current.revision) "
                "FROM executive_department_plans current WHERE current.team=p.team)",
                (now,),
            ).fetchone()
            department_plans = []
            for row in department_plan_rows:
                plan = self._department_plan_record(*row)
                plan["status"] = "current"
                plan["review_state"] = "due" if plan["review_at"] <= now else "scheduled"
                department_plans.append(plan)
            department_lease_count = self.db.execute(
                "SELECT COUNT(*) FROM executive_department_leases"
            ).fetchone()[0]
            department_lease_active_count = self.db.execute(
                "SELECT COUNT(*) FROM executive_department_leases "
                "WHERE status='active' AND lease_until > ?",
                (now,),
            ).fetchone()[0]
            department_leases = []
            for row in self.db.execute(
                "SELECT body FROM executive_department_leases ORDER BY team LIMIT ?",
                (open_limit,),
            ):
                department_lease = json.loads(row[0])
                if (
                    department_lease["status"] == "active"
                    and department_lease["lease_until"] <= now
                ):
                    department_lease["status"] = "expired"
                department_leases.append(department_lease)
            department_conflict_count = self.db.execute(
                "SELECT COUNT(*) FROM executive_department_conflicts"
            ).fetchone()[0]
            department_conflict_open_count = self.db.execute(
                "SELECT COUNT(*) FROM executive_department_conflicts c "
                "LEFT JOIN executive_conflict_adjudications a ON "
                "a.conflict_id=c.id AND a.revision=(SELECT MAX(x.revision) "
                "FROM executive_conflict_adjudications x WHERE x.conflict_id=c.id) "
                "WHERE NOT EXISTS (SELECT 1 FROM executive_department_conflicts newer "
                "WHERE newer.supersedes_id=c.id) AND (a.conflict_id IS NULL "
                "OR a.disposition IN ('request_replan','defer') "
                ") AND c.team_a_revision=(SELECT COALESCE(MAX(p.revision),0) "
                "FROM executive_department_plans p WHERE p.team=c.team_a) "
                "AND c.team_b_revision=(SELECT COALESCE(MAX(p.revision),0) "
                "FROM executive_department_plans p WHERE p.team=c.team_b)"
            ).fetchone()[0]
            department_conflict_stale_count = self.db.execute(
                "SELECT COUNT(*) FROM executive_department_conflicts c "
                "WHERE NOT EXISTS (SELECT 1 FROM executive_department_conflicts newer "
                "WHERE newer.supersedes_id=c.id) AND (c.team_a_revision<>(SELECT "
                "COALESCE(MAX(p.revision),0) "
                "FROM executive_department_plans p WHERE p.team=c.team_a) "
                "OR c.team_b_revision<>(SELECT COALESCE(MAX(p.revision),0) "
                "FROM executive_department_plans p WHERE p.team=c.team_b))"
            ).fetchone()[0]
            department_conflicts = [
                self._department_conflict_view_in_snapshot(row[0])
                for row in self.db.execute(
                    "SELECT id FROM executive_department_conflicts ORDER BY rowid DESC LIMIT ?",
                    (open_limit,),
                )
            ]
            return {
                "schema": "brain-executive-snapshot-v4",
                "role": role,
                "objectives": objectives,
                "tasks": [
                    {k: task[k] for k in ("id", "team", "title", "state", "epoch", "candidate_sha")}
                    for task in task_projection["tasks"]
                ],
                **rows,
                "department_plans": department_plans,
                "department_leases": department_leases,
                "department_conflicts": department_conflicts,
                "contract_review_items": contract_review_projection["items"],
                "events": list(reversed(events)),
                "accepted_evidence": accepted,
                "counts": {
                    **counts,
                    "tasks": task_projection["total_count"],
                    "active_tasks": task_projection["active_count"],
                    "omitted_active_tasks": task_projection["omitted_active_count"],
                    "task_states": task_projection["state_counts"],
                    "department_plans": department_plan_count,
                    "department_plan_reviews_due": department_plan_review_due_count,
                    "department_leases": department_lease_count,
                    "department_leases_active": department_lease_active_count,
                    "department_conflicts": department_conflict_count,
                    "department_conflicts_open": department_conflict_open_count,
                    "department_conflicts_stale": department_conflict_stale_count,
                    "contract_reviews_pending": contract_review_projection["pending_count"],
                    "contract_reviews_omitted": contract_review_projection["omitted_count"],
                },
                "known_unmeasured_acceptance": (
                    "Полная автономная работа организации ещё не подтверждена."
                ),
            }

    def render_report(self, *, history_limit=5, open_limit=10, max_chars=6000):
        if type(max_chars) is not int or not 200 <= max_chars <= 20000:
            raise ExecutiveError("max_chars must be between 200 and 20000")
        view = self.snapshot(history_limit=history_limit, open_limit=open_limit)
        role = view["role"]
        lines = ["Организация Brain: краткий отчёт"]
        if role:
            lines.append(
                f"CEO: {role['owner']}; эпоха {role['epoch']}; {role['status']}; Astra High."
            )
        else:
            lines.append("CEO: эпизод ещё не назначен.")
        states = view["counts"]["task_states"]
        lines.append(
            "Задачи: "
            + (", ".join(f"{key}: {value}" for key, value in sorted(states.items())) or "нет")
        )
        if view["counts"]["omitted_active_tasks"]:
            lines.append(
                "Показана ограниченная активная выборка: "
                f"{len(view['tasks'])} из {view['counts']['active_tasks']}; "
                "остальные сохранены в журнале."
            )
        lines.append(f"Открытые обязательства: {view['counts']['obligations']}.")
        lines.append(
            "Планы команд: "
            f"{view['counts']['department_plans']}; срок пересмотра наступил: "
            f"{view['counts']['department_plan_reviews_due']}."
        )
        lines.append(
            "Активные team leases: "
            f"{view['counts']['department_leases_active']} из "
            f"{view['counts']['department_leases']}."
        )
        lines.append(
            "Открытые конфликты отделов: "
            f"{view['counts']['department_conflicts_open']} из "
            f"{view['counts']['department_conflicts']}."
        )
        lines.append(
            f"Конфликты с устаревшими планами: {view['counts']['department_conflicts_stale']}."
        )
        lines.append(
            "Независимые подтверждения переходов контрактов: "
            f"{view['counts']['contract_reviews_pending']}; "
            f"не показано: {view['counts']['contract_reviews_omitted']}."
        )
        lines.extend(
            f"- {row['id']}: {row['title']} ({row['status']})" for row in view["obligations"]
        )
        lines.extend(
            f"- {row['team']} v{row['revision']}: {row['summary']} "
            f"(owner {row['owner']}; review {row['review_state']})"
            for row in view["department_plans"]
        )
        lines.extend(
            f"- {row['team']}: {row['owner']} epoch {row['epoch']} ({row['status']})"
            for row in view["department_leases"]
        )
        lines.extend(
            f"- {row['id']}: {row['status']} ({row['team_a']} vs {row['team_b']})"
            for row in view["department_conflicts"]
        )
        lines.extend(
            f"- {row['participant_team']}: {row['name']} v{row['from_version']}→"
            f"v{row['to_version']} ({row['contract_hash']})"
            for row in view["contract_review_items"]
        )
        if view["counts"]["contract_reviews_pending"]:
            lines.append(
                "Этот список только показывает недостающие отзывы; "
                "подтверждения не создаются автоматически."
            )
        lines.append(f"Ожидающие запросы: {view['counts']['requests']}.")
        lines.extend(f"- {row['id']}: {row['requested_change']}" for row in view["requests"])
        lines.append("Последние решения:")
        lines.extend(f"- {row['id']}: {row['summary']}" for row in view["decisions"])
        lines.append(view["known_unmeasured_acceptance"])
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 2] + "…\n"
