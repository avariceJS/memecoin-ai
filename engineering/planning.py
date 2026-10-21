"""Evidence-triggered Sol planning within an immutable, executive-approved catalogue.

Proposals are data. They cannot supply executable checks, file grants or policy.
A new capability envelope requires a separately reviewed controller release.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .git_gate import GitGate
from .metered_codex import metered_client
from .org_state import StateError, _json
from .supervisor import RULES, SOL, config, git, log_event


def eligible(cfg, db):
    """Only missing fixed catalogue cards with integrated, exact-version inputs."""
    known = {t["id"]: t for t in db.list_tasks()}
    cards = []
    for name in dict.fromkeys(cfg.get("catalogue", [])):
        card = cfg["tasks"][name]
        if name in known or not all(
            known.get(dep, {}).get("state") == "integrated" for dep in card["dependencies"]
        ):
            continue
        current = {key: db.get_contract(key) for key in card["contracts"]}
        if all(
            current[key] is not None and current[key]["version"] == version
            for key, version in card["contracts"].items()
        ):
            cards.append(name)
    return cards


def _task_sha256(card):
    return hashlib.sha256(_json(card).encode("utf-8")).hexdigest()


def _proposal(proposal, *, allow_defer=False):
    if not isinstance(proposal, dict) or set(proposal) != {"card", "rationale", "evidence"}:
        raise StateError("planning_proposal_shape")
    if not isinstance(proposal["card"], str) and not (allow_defer and proposal["card"] is None):
        raise StateError("planning_card_type")
    if not all(
        isinstance(proposal[k], str)
        and 10 <= len(proposal[k].strip()) <= 4000
        and len(proposal[k]) <= 4000
        for k in ("rationale", "evidence")
    ):
        raise StateError("planning_requires_bounded_reason_and_evidence")


def _admit_in_transaction(cfg, db, proposal, base, *, department_nomination, executive):
    """Append an already-validated trusted card inside the caller's transaction."""
    _proposal(proposal)
    name = proposal["card"]
    if name not in eligible(cfg, db):
        raise StateError("task_outside_current_objective_envelope")
    if db.get_decision("admission/" + name) is not None:
        raise StateError("admission_record_already_exists")
    card = cfg["tasks"][name]
    executive.validate_department_plan_nomination(
        department_nomination,
        name,
        "offline-brain-engineering",
        card_sha256=_task_sha256(card),
    )
    if department_nomination["team"] != card["team"]:
        raise StateError("department plan nomination team mismatch")
    db.db.execute(
        "INSERT INTO tasks(id,team,title,brief,scope,acceptance,dependencies,contracts,"
        "base_sha,objective_id,max_attempts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            name,
            card["team"],
            card["title"],
            card["brief"],
            _json(card["scope"]),
            _json(["Frozen independent acceptance", "Regression checks", "Independent review"]),
            _json(card["dependencies"]),
            _json(card["contracts"]),
            base,
            "offline-brain-engineering",
            3,
        ),
    )
    db._event("task_added", name, db.get_task(name))
    body = {"proposal": proposal, "base": base}
    body["department_nomination"] = department_nomination
    db.db.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?)",
        ("admission/" + name, 1, _json(body), None, None),
    )
    db._event(
        "decision_recorded",
        "admission/" + name,
        {"version": 1, "body": body, "replaces": None, "replaces_version": None},
    )
    return name


def admit(cfg, db, proposal, base, *, department_nomination):
    """Atomically append the trusted card and its admission rationale.

    Store's public writers own their transactions and cannot be nested. This is a
    deliberately narrow Store-schema adapter: no field comes from a generated
    proposal except bounded rationale/evidence and a validated catalogue key.
    """
    _proposal(proposal)
    from .executive import Executive

    executive = Executive(db)
    with db._tx():
        return _admit_in_transaction(
            cfg,
            db,
            proposal,
            base,
            department_nomination=department_nomination,
            executive=executive,
        )


def require_controller_assignment(cfg, db, task):
    """Require durable controller admission before dispatch or acceptance.

    Configurations without a fixed catalogue are legacy unit/development fixtures. In a
    controller deployment, a task must either be a nominated fixed card or carry the
    separately validated task-spec admission. This check deliberately validates the
    persisted admission-time evidence instead of requiring the department lease to remain
    live for the full worker and review lifecycle.
    """
    if "catalogue" not in cfg:
        return
    task_id = task.get("id") if type(task) is dict else None
    if type(task_id) is not str or not task_id:
        raise StateError("controller_task_identity_invalid")
    catalogue = cfg.get("catalogue")
    if type(catalogue) is not list or any(type(item) is not str for item in catalogue):
        raise StateError("controller_catalogue_invalid")
    if task_id not in catalogue:
        if db.get_task_spec(task_id) is None:
            raise StateError("task_outside_controller_admission_scope")
        return

    card = cfg.get("tasks", {}).get(task_id)
    if type(card) is not dict:
        raise StateError("fixed_card_configuration_missing")
    decision = db.get_decision("admission/" + task_id)
    if type(decision) is not dict or decision.get("version") != 1:
        raise StateError("fixed_card_admission_missing")
    body = decision.get("body")
    if type(body) is not dict or set(body) != {"proposal", "base", "department_nomination"}:
        raise StateError("fixed_card_admission_invalid")
    proposal = body.get("proposal")
    if type(proposal) is not dict or proposal.get("card") != task_id:
        raise StateError("fixed_card_admission_invalid")
    digest = _task_sha256(card)
    nomination = body.get("department_nomination")
    if (
        type(nomination) is not dict
        or nomination.get("schema") != "department-plan-nomination-v1"
        or nomination.get("card_id") != task_id
        or nomination.get("team") != card.get("team")
        or nomination.get("objective_id") != "offline-brain-engineering"
        or type(nomination.get("evidence_refs")) is not list
        or "catalogue-card-sha256:" + task_id + ":" + digest
        not in nomination.get("evidence_refs", [])
    ):
        raise StateError("fixed_card_department_nomination_invalid")
    expected = {
        "team": card.get("team"),
        "title": card.get("title"),
        "brief": card.get("brief", ""),
        "scope": card.get("scope", []),
        "dependencies": card.get("dependencies", []),
        "contracts": card.get("contracts", {}),
        "base_sha": body.get("base"),
        "objective_id": "offline-brain-engineering",
        "acceptance": [
            "Frozen independent acceptance",
            "Regression checks",
            "Independent review",
        ],
        "max_attempts": 3,
    }
    if any(task.get(key) != value for key, value in expected.items()):
        raise StateError("fixed_card_task_drift")


def _unique_proposal_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("planning_duplicate_json_key")
        result[key] = value
    return result


def _parse_proposal(text):
    value = text.strip()
    if value.startswith("```"):
        value = "\n".join(value.splitlines()[1:-1])
    return json.loads(value, object_pairs_hook=_unique_proposal_object)


def _finish_plan(cfg, db, key, proposal, base, thread, *, nominations, executive):
    """Commit fixed-card admission and its terminal planning decision together."""
    _proposal(proposal, allow_defer=True)
    with db._tx():
        nomination = nominations.get(proposal["card"])
        if proposal["card"] is not None and nomination is None:
            raise StateError("task card has no current department nomination")
        admitted = (
            _admit_in_transaction(
                cfg,
                db,
                proposal,
                base,
                department_nomination=nomination,
                executive=executive,
            )
            if proposal["card"] is not None
            else None
        )
        db._put_decision_in_transaction(
            key,
            {
                "thread": thread,
                "model": SOL,
                "proposal": proposal,
                "admitted": admitted,
                "department_nomination": nomination,
                "requires_executive_review": False,
                "status": "admitted" if admitted else "deferred",
            },
        )
    return admitted


def plan_next(state: Path, db):
    cfg = config(state)
    eligible_cards = eligible(cfg, db)
    if not eligible_cards:
        return None
    from .executive import Executive

    executive = Executive(db)
    card_teams = {name: cfg["tasks"][name]["team"] for name in eligible_cards}
    card_sha256s = {name: _task_sha256(cfg["tasks"][name]) for name in eligible_cards}
    nomination_snapshot = executive.department_plan_nominations(
        card_teams, "offline-brain-engineering", card_sha256s=card_sha256s
    )
    nominations = nomination_snapshot["nominations"]
    cards = [name for name in eligible_cards if name in nominations]
    if not cards:
        return None
    tasks = [{k: t[k] for k in ("id", "title", "state", "candidate_sha")} for t in db.list_tasks()]
    dossier = {
        "results": tasks,
        "available_cards": {k: cfg["tasks"][k] for k in cards},
        "department_nominations": {k: nominations[k] for k in cards},
        "department_state_sha256": nomination_snapshot["state_sha256"],
    }
    planning_teams = sorted({cfg["tasks"][name]["team"] for name in cards})
    planning_team = planning_teams[0] if len(planning_teams) == 1 else "Organization"
    fingerprint = hashlib.sha256(json.dumps(dossier, sort_keys=True).encode()).hexdigest()
    key = "planning/" + fingerprint
    # At most one model episode per evidence fingerprint, even if two trusted
    # callers race. A crash leaves requested evidence for executive review.
    with db._tx():
        if db.get_decision(key):
            return None
        requested = {
            "model": SOL,
            "status": "requested",
            "cards": cards,
            "requires_executive_review": True,
        }
        db.db.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?)", (key, 1, _json(requested), None, None)
        )
        db._event(
            "decision_recorded",
            key,
            {"version": 1, "body": requested, "replaces": None, "replaces_version": None},
        )
    thread = None
    finalized = False
    admitted = None
    try:
        native = state / "planning" / fingerprint
        native.mkdir(parents=True, exist_ok=True)
        with metered_client(
            state,
            native,
            cfg=cfg,
            context={"team": planning_team, "role": "planner", "task": "planning/" + fingerprint},
            event_sink=lambda e: log_event(state, "codex", role="planner", event=e),
        ) as c:
            thread = c.start(
                SOL, RULES + "\nYou are the cross-department catalogue planning coordinator."
            )
            result = c.turn(
                thread,
                "Choose a useful next catalogue task justified by integrated evidence, or defer. "
                "No tools or implementation. Only existing cards may be chosen; never manufacture "
                "work or claim a defect has been reproduced if no experiment did so. "
                "Return JSON {card: string|null, rationale:string, evidence:string}.\n"
                + json.dumps(dossier),
                dispatch_id="planning-proposal:" + fingerprint,
                model=SOL,
                estimate={"tokens": 3500, "wpu": 1},
                tool=lambda _: {"error": "planning_has_no_execution_capability"},
                deadline_seconds=180,
                output_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "card": {"type": ["string", "null"]},
                        "rationale": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["card", "rationale", "evidence"],
                },
            )
            try:
                proposal = _parse_proposal(result["text"])
                _proposal(proposal, allow_defer=True)
            except (ValueError, TypeError) as exc:
                db.put_decision(
                    key,
                    {
                        "thread": thread,
                        "model": SOL,
                        "status": "rejected",
                        "requires_executive_review": True,
                        "error": type(exc).__name__ + ":" + str(exc)[:500],
                    },
                )
                log_event(state, "planning_rejected", thread=thread, error=str(exc)[:500])
                return None
            admitted = _finish_plan(
                cfg,
                db,
                key,
                proposal,
                git(state / "integration", "rev-parse", GitGate.ACTIVE_REF)
                if proposal["card"] is not None
                else None,
                thread,
                nominations=nominations,
                executive=executive,
            )
            finalized = True
            log_event(state, "planning_completed", thread=thread, admitted=admitted)
            return admitted
    except Exception as exc:
        if finalized:
            # Preserve the committed task/decision, but keep cleanup failures visible
            # so the caller can stop instead of continuing as if cleanup succeeded.
            raise
        db.put_decision(
            key,
            {
                "thread": thread,
                "model": SOL,
                "status": "interrupted",
                "requires_executive_review": True,
                "error": type(exc).__name__ + ":" + str(exc)[:500],
            },
        )
        log_event(state, "planning_interrupted", thread=thread, error=str(exc)[:500])
        return None
