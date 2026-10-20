"""Short, replaceable Astra executive episodes over durable organization state."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .executive import Executive
from .supervisor import (
    atomic,
    render_desk,
    store,
    structured,
)

ASTRA = "gpt-6-astra"
EPISODE_SECONDS = 120
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
        "next_horizon": {"type": "string", "minLength": 1, "maxLength": 1000},
        "limitations": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
    "required": ["summary", "rationale", "next_horizon", "limitations"],
    "additionalProperties": False,
}


def validate_response(text):
    result = structured(text)
    if not isinstance(result, dict) or set(result) != set(OUTPUT_SCHEMA["properties"]):
        raise ValueError("executive_response_fields")
    for name, rule in OUTPUT_SCHEMA["properties"].items():
        value = result[name]
        if not isinstance(value, str) or not value.strip() or len(value) > rule["maxLength"]:
            raise ValueError("executive_response_value")
    return result


def bounded_dossier(dossier, max_chars=24000):
    """Compact projections only; durable records remain complete in SQLite."""

    def compact(value):
        if isinstance(value, str):
            return value if len(value) <= 500 else value[:499] + "…"
        if isinstance(value, list):
            return [compact(item) for item in value[:20]]
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        return value

    packet = compact(dossier)
    packet["projection_limits"] = {
        "max_chars": max_chars,
        "max_text_chars": 500,
        "max_list_items": 20,
        "notice": "Bounded projection; inspect durable state before closing omitted obligations.",
    }
    organization = packet["organization"]
    while len(json.dumps(packet, ensure_ascii=False, sort_keys=True)) > max_chars:
        fields = ("events", "decisions", "accepted_evidence", "tasks", "requests", "obligations")
        field = next((name for name in fields if organization.get(name)), None)
        if field is None:
            raise ValueError("executive_dossier_exceeds_bound")
        organization[field].pop(0)
    return packet


def chief(state: Path, *, trigger="owner-review"):
    """Run one bounded episode, release CEO lease, and leave workers independent.

    The caller owns resource admission and trigger deduplication. This function never
    changes deployment policy, worker tasks, publication, or request approval state.
    """
    state = state.resolve()
    if (state / "STOP").exists():
        return {"status": "stopped"}
    db = store(state)
    executive = Executive(db)
    owner = f"astra-{os.getpid()}-{time.time_ns()}"
    lease = executive.claim(owner, model=ASTRA, reasoning="high", lease_seconds=180)
    if lease is None:
        db.close()
        return {"status": "executive_busy"}
    try:
        for obligation_id, title, owner_role, team in (
            (
                "ORG-ACCEPT-01",
                "Пройти первый операционный этап с независимой приёмкой.",
                "CEO",
                None,
            ),
            (
                "ORG-CONTINUE-01",
                "Проверить durable причины остановки и bounded follow-up.",
                "lead",
                "Platform",
            ),
            (
                "ORG-LONGRUN-01",
                "Измерить длительную работу до заявлений о днях и неделях.",
                "CEO",
                None,
            ),
        ):
            if executive.get_obligation(obligation_id) is None:
                executive.obligation(
                    lease,
                    obligation_id,
                    title,
                    owner_role=owner_role,
                    team=team,
                    evidence_refs=["founder-directive:engineering/OWNER-DESK.md:2026-10-19"],
                )
        if (
            trigger == "catalogue-exhausted"
            and executive.get_obligation("next-policy-horizon") is None
        ):
            executive.obligation(
                lease,
                "next-policy-horizon",
                "Подготовить следующий полезный инженерный горизонт и проверенную policy.",
                owner_role="CEO",
                rationale=(
                    "Допустимый каталог исчерпан. Это инженерная обязанность директора; "
                    "новые полномочия или ресурсы требуют отдельного подтверждения."
                ),
                evidence_refs=["deployment:catalogue-exhausted"],
            )
        if trigger == "blocked-dependencies":
            pending = [task for task in db.list_tasks() if task["state"] == "pending"]
            blockers = [
                {key: task[key] for key in ("id", "dependencies", "contracts")} for task in pending
            ]
            fingerprint = hashlib.sha256(json.dumps(blockers, sort_keys=True).encode()).hexdigest()
            obligation_id = "blocked-dependencies/" + fingerprint
            if executive.get_obligation(obligation_id) is None:
                executive.obligation(
                    lease,
                    obligation_id,
                    "Разрешить зависимости остановившейся очереди без расширения полномочий.",
                    owner_role="CEO",
                    rationale="Свободных исполнимых задач нет; pending-задачи остаются в реестре.",
                    evidence_refs=[
                        "organization.sqlite:tasks:" + task["id"] for task in pending[:20]
                    ],
                    blocked_by=["task:" + task["id"] for task in pending[:20]],
                )
        executive.release(lease, summary="model call blocked by current policy")
        render_desk(state, db)
        atomic(
            state / "chief.json",
            {
                "epoch": lease["epoch"],
                "model": ASTRA,
                "reasoning": "high",
                "thread": None,
                "status": "blocked",
                "trigger": trigger,
                "reason": "manual chief inference is not authorized",
            },
        )
        return {
            "status": "blocked",
            "epoch": lease["epoch"],
            "reason": "manual chief inference is not authorized",
        }

    finally:
        try:
            db.close()
        except Exception:
            pass
