"""Evidence-triggered Sol planning within an immutable, executive-approved catalogue.

Proposals are data. They cannot supply executable checks, file grants or policy.
A new capability envelope requires a separately reviewed controller release.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .metered_codex import metered_client
from .org_state import StateError, _json
from .supervisor import RULES, SOL, config, git, log_event, structured


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


def admit(cfg, db, proposal, base):
    """Atomically append the trusted card and its admission rationale.

    Store's public writers own their transactions and cannot be nested. This is a
    deliberately narrow Store-schema adapter: no field comes from a generated
    proposal except bounded rationale/evidence and a validated catalogue key.
    """
    _proposal(proposal)
    name = proposal["card"]
    with db._tx():
        if name not in eligible(cfg, db):
            raise StateError("task_outside_current_objective_envelope")
        if db.get_decision("admission/" + name) is not None:
            raise StateError("admission_record_already_exists")
        card = cfg["tasks"][name]
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


def plan_next(state: Path, db):
    cfg = config(state)
    cards = eligible(cfg, db)
    if not cards:
        return None
    tasks = [{k: t[k] for k in ("id", "title", "state", "candidate_sha")} for t in db.list_tasks()]
    dossier = {"results": tasks, "available_cards": {k: cfg["tasks"][k] for k in cards}}
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
    try:
        native = state / "planning" / fingerprint
        native.mkdir(parents=True, exist_ok=True)
        with metered_client(
            state,
            state / "integration",
            cfg=cfg,
            context={
                "role": "planner",
                "team": "Platform",
                "task": "catalogue-planning",
                "episode_id": f"planning:{fingerprint}:1",
                "authority": "CEO-20261020-02",
            },
            harness_cwd=native,
            event_sink=lambda e: log_event(state, "codex", role="planner", event=e),
        ) as c:
            thread = c.start(SOL, RULES + "\nYou are the Platform department planning lead.")
            result = c.turn(
                thread,
                "Choose a useful next catalogue task justified by integrated evidence, or defer. "
                "No tools or implementation. Only existing cards may be chosen; never manufacture "
                "work or claim a defect has been reproduced if no experiment did so. "
                "Return JSON {card: string|null, rationale:string, evidence:string}.\n"
                + json.dumps(dossier),
                dispatch_id=f"planning:{fingerprint}:1:turn",
                model=SOL,
                estimate={"tokens": 12000, "wpu": 1.0},
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
                proposal = structured(result["text"])
                _proposal(proposal, allow_defer=True)
                admitted = (
                    admit(cfg, db, proposal, git(state / "integration", "rev-parse", "HEAD"))
                    if proposal["card"] is not None
                    else None
                )
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
            db.put_decision(
                key,
                {
                    "thread": thread,
                    "model": SOL,
                    "proposal": proposal,
                    "admitted": admitted,
                    "requires_executive_review": False,
                    "status": "admitted" if admitted else "deferred",
                },
            )
            log_event(state, "planning_completed", thread=thread, admitted=admitted)
            return admitted
    except Exception as exc:
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
