"""Episodic CEO governance on the engineering Store's durable journal.

This module records responsibility, decisions and requests. It grants no tools,
execution permissions, budget, policy changes, or scheduling authority. Pending
requests never change task readiness. All writes require the current CEO fence;
conversations are disposable and open obligations survive their replacement.
"""

from __future__ import annotations

import json

from .org_state import StateError, Store, _json, _name, _positive

MODEL = "gpt-6-astra"
REASONING = "high"


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
            self.db.execute(
                f"INSERT INTO executive_{table} VALUES(?,?)", (record_id, _json(record))
            )
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
        return self._create(lease, "obligations", obligation_id, body)

    def transition_obligation(
        self, lease, obligation_id: str, status: str, *, completion_evidence=(), blocked_by=()
    ):
        if status not in ("open", "blocked", "completed", "cancelled"):
            raise ExecutiveError("unknown obligation status")
        evidence, blockers = _refs(completion_evidence), _refs(blocked_by)
        if status == "completed" and not evidence:
            raise ExecutiveError("completion requires evidence references")
        if status == "blocked" and not blockers:
            raise ExecutiveError("blocked obligation requires blockers")
        if status != "blocked" and blockers:
            raise ExecutiveError("only blocked obligations may retain blockers")
        return self._transition(
            lease,
            "obligations",
            obligation_id,
            status,
            {"completion_evidence": evidence, "blocked_by": blockers},
            terminal=("completed", "cancelled"),
        )

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
            if role and role["status"] == "active" and role["lease_until"] <= self.store.clock():
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
            tasks = self.store.list_tasks()
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
            return {
                "schema": "brain-executive-snapshot-v1",
                "role": role,
                "objectives": objectives,
                "tasks": [
                    {k: task[k] for k in ("id", "team", "title", "state", "epoch", "candidate_sha")}
                    for task in tasks[:100]
                ],
                **rows,
                "events": list(reversed(events)),
                "accepted_evidence": accepted,
                "counts": {**counts, "tasks": len(tasks)},
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
        states = {}
        for task in view["tasks"]:
            states[task["state"]] = states.get(task["state"], 0) + 1
        lines.append(
            "Задачи: "
            + (", ".join(f"{key}: {value}" for key, value in sorted(states.items())) or "нет")
        )
        lines.append(f"Открытые обязательства: {view['counts']['obligations']}.")
        lines.extend(
            f"- {row['id']}: {row['title']} ({row['status']})" for row in view["obligations"]
        )
        lines.append(f"Ожидающие запросы: {view['counts']['requests']}.")
        lines.extend(f"- {row['id']}: {row['requested_change']}" for row in view["requests"])
        lines.append("Последние решения:")
        lines.extend(f"- {row['id']}: {row['summary']}" for row in view["decisions"])
        lines.append(view["known_unmeasured_acceptance"])
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 2] + "…\n"
