"""Controller-owned, replay-safe department planning over fixed catalogue cards.

The transport is always injected. This module never creates a provider client or
grants task execution authority; it only records fenced department plans.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from .executive import DEPARTMENT_MODEL, DEPARTMENT_REASONING, Executive, ExecutiveError
from .org_state import StateError, Store, _json
from .planning import eligible
from .supervisor import config

OBJECTIVE_ID = "offline-brain-engineering"
PLAN_OWNER = "luna-controller-department-planner"
DISPATCH_PREFIX = "department-plan-cycle/"
MAX_DISPATCH_BYTES = 128_000
MAX_RESPONSE_BYTES = 32_000


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _menu(cfg, db, objective_id):
    cards = {}
    for card_id in eligible(cfg, db):
        card = cfg["tasks"][card_id]
        cards[card_id] = {
            "id": card_id,
            "team": card["team"],
            "title": card["title"],
            "brief": card["brief"],
            "dependencies": card.get("dependencies", []),
            "contracts": card.get("contracts", {}),
            "task_sha256": _digest(card),
        }
    teams = {}
    for card_id, card in sorted(cards.items()):
        teams.setdefault(card["team"], {})[card_id] = card
    return {
        "objective_id": objective_id,
        "store_revision": db.quiescence_snapshot()["revision"],
        "teams": teams,
    }


def _latest_dispatch(db, request_id):
    return db.get_decision(DISPATCH_PREFIX + request_id)


def _append(db, request_id, body, *, expected_status=None):
    key = DISPATCH_PREFIX + request_id
    with db._tx():
        current = db.get_decision(key)
        if current is None:
            if expected_status is not None:
                raise StateError("department plan dispatch changed")
        elif expected_status is None or current["body"].get("status") != expected_status:
            raise StateError("department plan dispatch changed")
        db._put_decision_in_transaction(key, body)
        return db.get_decision(key)


def _unresolved_dispatch_for_team(db, team):
    prefix_end = DISPATCH_PREFIX[:-1] + "0"
    with db._read_snapshot():
        rows = db.db.execute(
            "SELECT d.name,d.version,d.body FROM decisions d JOIN ("
            "SELECT name,MAX(version) AS version FROM decisions "
            "WHERE name>=? AND name<? GROUP BY name ORDER BY name LIMIT 101"
            ") latest ON latest.name=d.name AND latest.version=d.version "
            "ORDER BY d.name",
            (DISPATCH_PREFIX, prefix_end),
        ).fetchall()
    if len(rows) > 100:
        return {"status": "history_overflow", "team": team}
    for row in rows:
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError, RecursionError):
            return {"status": "history_invalid", "team": team}
        if type(body) is not dict or body.get("team") != team:
            continue
        if body.get("status") in ("dispatching", "outcome_unknown"):
            return body
    return None


def _response_recorded_for_team(db, team, menu_sha256):
    prefix_end = DISPATCH_PREFIX[:-1] + "0"
    with db._read_snapshot():
        rows = db.db.execute(
            "SELECT d.name,d.version,d.body FROM decisions d JOIN ("
            "SELECT name,MAX(version) AS version FROM decisions "
            "WHERE name>=? AND name<? GROUP BY name ORDER BY name LIMIT 101"
            ") latest ON latest.name=d.name AND latest.version=d.version "
            "ORDER BY d.name",
            (DISPATCH_PREFIX, prefix_end),
        ).fetchall()
    if len(rows) > 100:
        return None
    for row in rows:
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError, RecursionError):
            return None
        if (
            type(body) is dict
            and body.get("team") == team
            and body.get("menu_sha256") == menu_sha256
            and body.get("status") == "response_recorded"
        ):
            return row["name"].removeprefix(DISPATCH_PREFIX), body
    return None


def _validate_response(response, team_menu):
    if type(response) is not dict or set(response) != {"summary", "rationale", "priorities"}:
        raise ExecutiveError("department plan response shape is invalid")
    summary, rationale, priorities = (
        response["summary"],
        response["rationale"],
        response["priorities"],
    )
    allowed_priorities = {"catalogue-card:" + card_id for card_id in team_menu}
    if (
        type(summary) is not str
        or not summary.strip()
        or len(summary) > 2000
        or type(rationale) is not str
        or len(rationale) > 2000
        or type(priorities) is not list
        or not priorities
        or len(priorities) > 20
        or any(type(item) is not str or item not in allowed_priorities for item in priorities)
        or len(set(priorities)) != len(priorities)
    ):
        raise ExecutiveError("department plan response is outside the trusted menu")
    return summary, rationale, priorities


def _currentness(state, db, objective_id, expected_menu_sha256):
    if (Path(state) / "STOP").exists():
        return False
    current_cfg = config(Path(state))
    current_menu = _menu(current_cfg, db, objective_id)
    return _digest(current_menu) == expected_menu_sha256


def _commit_response(
    executive,
    db,
    state,
    objective_id,
    team,
    lease,
    revision,
    review_at,
    menu_sha256,
    store_revision,
    request_id,
    raw_response,
    stop_guard,
):
    try:
        encoded = _json(raw_response).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise ExecutiveError("department plan response exceeds byte limit")
        detached = json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
        rejected = {
            "schema": "department-plan-dispatch-v1",
            "team": team,
            "objective_id": objective_id,
            "owner_epoch": lease["epoch"],
            "menu_sha256": menu_sha256,
            "status": "rejected",
            "error": "department plan response is not bounded JSON",
        }
        _append(db, request_id, rejected, expected_status="dispatching")
        return False
    response_bytes = _json(detached).encode("utf-8")
    recorded = {
        "schema": "department-plan-dispatch-v1",
        "team": team,
        "objective_id": objective_id,
        "owner_epoch": lease["epoch"],
        "plan_revision": revision,
        "review_at": review_at,
        "menu_sha256": menu_sha256,
        "store_revision": store_revision,
        "status": "response_recorded",
        "response": detached,
        "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
    }
    _append(db, request_id, recorded, expected_status="dispatching")
    return _replay_response(
        executive,
        db,
        state,
        objective_id,
        team,
        lease,
        revision,
        review_at,
        menu_sha256,
        request_id,
        recorded,
        stop_guard,
    )


def _replay_response(
    executive,
    db,
    state,
    objective_id,
    team,
    lease,
    revision,
    review_at,
    menu_sha256,
    request_id,
    record,
    stop_guard,
):
    if (
        record.get("status") != "response_recorded"
        or record.get("owner_epoch") != lease["epoch"]
        or record.get("team") != team
        or record.get("objective_id") != objective_id
        or record.get("menu_sha256") != menu_sha256
        or not stop_guard()
        or not _currentness(state, db, objective_id, menu_sha256)
    ):
        return False
    response = record.get("response")
    response_bytes = _json(response).encode("utf-8")
    if hashlib.sha256(response_bytes).hexdigest() != record.get("response_sha256"):
        _append(
            db,
            request_id,
            {**record, "status": "rejected", "error": "department response custody mismatch"},
            expected_status="response_recorded",
        )
        return False
    current_menu = _menu(config(Path(state)), db, objective_id)
    team_menu = current_menu["teams"].get(team, {})
    try:
        summary, rationale, priorities = _validate_response(response, team_menu)
        priority_card_ids = [item.removeprefix("catalogue-card:") for item in priorities]
        evidence_refs = [
            "department-menu-sha256:" + menu_sha256,
            "objective:" + objective_id,
            *sorted(priorities),
            *[
                "catalogue-card-sha256:" + card_id + ":" + team_menu[card_id]["task_sha256"]
                for card_id in sorted(priority_card_ids)
            ],
        ]
        executive.record_department_plan(
            lease,
            revision,
            objective_id,
            summary,
            priorities,
            review_at,
            rationale=rationale,
            evidence_refs=evidence_refs,
            expected_store_revision=record["store_revision"],
        )
    except (ExecutiveError, StateError, TypeError, ValueError) as exc:
        _append(
            db,
            request_id,
            {**record, "status": "rejected", "error": type(exc).__name__ + ":" + str(exc)[:300]},
            expected_status="response_recorded",
        )
        return False
    _append(db, request_id, {**record, "status": "planned"}, expected_status="response_recorded")
    return True


def ensure_department_plans(
    state: Path,
    db: Store,
    transport: Callable[[dict], dict] | None,
    *,
    stop_guard: Callable[[], bool],
    objective_id=OBJECTIVE_ID,
):
    """Create/replay current department plans only through an explicitly injected transport."""
    if transport is None or not stop_guard():
        return False
    if not db.db.execute("SELECT 1 FROM objectives WHERE id=?", (objective_id,)).fetchone():
        return False
    cfg = config(Path(state))
    menu = _menu(cfg, db, objective_id)
    menu_sha256 = _digest(menu)
    executive = Executive(db)
    progressed = False
    for team, team_menu in sorted(menu["teams"].items()):
        plan = executive.get_department_plan(team)
        menu_ref = "department-menu-sha256:" + menu_sha256
        lease = executive.get_department_lease(team)
        current_lease = bool(
            lease
            and lease["status"] == "active"
            and lease["lease_until"] > db.clock()
            and plan is not None
            and plan["owner"] == lease["owner"]
            and plan["owner_epoch"] == lease["epoch"]
        )
        if (
            plan is not None
            and plan.get("status") == "current"
            and plan.get("review_state") == "scheduled"
            and menu_ref in plan["evidence_refs"]
            and current_lease
        ):
            recorded = _response_recorded_for_team(db, team, menu_sha256)
            if recorded is not None:
                request_id, body = recorded
                if body.get("owner_epoch") == lease["epoch"]:
                    if _replay_response(
                        executive,
                        db,
                        state,
                        objective_id,
                        team,
                        lease,
                        body["plan_revision"],
                        body["review_at"],
                        menu_sha256,
                        request_id,
                        body,
                        stop_guard,
                    ):
                        progressed = True
            continue
        if _unresolved_dispatch_for_team(db, team) is not None:
            continue
        lease = executive.get_department_lease(team)
        if lease and lease["status"] == "active" and lease["lease_until"] > db.clock():
            if lease["owner"] != PLAN_OWNER:
                continue
            lease = {
                key: lease[key]
                for key in (
                    "team",
                    "epoch",
                    "owner",
                    "model",
                    "reasoning",
                    "lease_until",
                    "status",
                )
            }
        else:
            lease = executive.claim_department(team, PLAN_OWNER)
            if lease is None:
                continue
        revision = (plan["revision"] + 1) if plan is not None else 1
        request_identity = {
            "team": team,
            "objective_id": objective_id,
            "owner_epoch": lease["epoch"],
            "plan_revision": revision,
            "menu_sha256": menu_sha256,
            "store_revision": menu["store_revision"],
        }
        request_id = _digest(request_identity)
        existing = _latest_dispatch(db, request_id)
        review_at = db.clock() + 3600
        if existing is not None and existing["body"].get("status") == "response_recorded":
            if _replay_response(
                executive,
                db,
                state,
                objective_id,
                team,
                lease,
                revision,
                existing["body"].get("review_at", review_at),
                menu_sha256,
                request_id,
                existing["body"],
                stop_guard,
            ):
                progressed = True
            continue
        if existing is not None and existing["body"].get("status") in (
            "dispatching",
            "outcome_unknown",
            "planned",
            "rejected",
        ):
            continue
        intent = {
            "schema": "department-plan-dispatch-v1",
            **request_identity,
            "status": "dispatching",
            "review_at": review_at,
            "request": {
                **request_identity,
                "model": DEPARTMENT_MODEL,
                "reasoning": DEPARTMENT_REASONING,
                "cards": team_menu,
                "lease": lease,
            },
            "request_sha256": _digest(
                {
                    **request_identity,
                    "model": DEPARTMENT_MODEL,
                    "reasoning": DEPARTMENT_REASONING,
                    "cards": team_menu,
                    "lease": lease,
                }
            ),
        }
        if len(_json(intent).encode("utf-8")) > MAX_DISPATCH_BYTES:
            continue
        if not stop_guard():
            return progressed
        if existing is None:
            try:
                _append(db, request_id, intent)
            except StateError:
                # Another controller won the same immutable request identity.
                # Its durable intent owns the one permitted transport attempt.
                continue
        elif existing["body"].get("status") == "cancelled_before_dispatch":
            _append(db, request_id, intent, expected_status="cancelled_before_dispatch")
        else:
            continue
        if not stop_guard() or not _currentness(state, db, objective_id, menu_sha256):
            _append(
                db,
                request_id,
                {**intent, "status": "cancelled_before_dispatch"},
                expected_status="dispatching",
            )
            return progressed
        try:
            response = transport(json.loads(_json(intent["request"])))
        except Exception as exc:
            _append(
                db,
                request_id,
                {
                    **intent,
                    "status": "outcome_unknown",
                    "error": type(exc).__name__ + ":" + str(exc)[:300],
                    "usage": "unknown",
                },
                expected_status="dispatching",
            )
            continue
        if _commit_response(
            executive,
            db,
            state,
            objective_id,
            team,
            lease,
            revision,
            review_at,
            menu_sha256,
            menu["store_revision"],
            request_id,
            response,
            stop_guard,
        ):
            progressed = True
    return progressed
