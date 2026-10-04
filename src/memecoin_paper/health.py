"""Read-only current service health, independent of a stale successful dashboard."""

import time
from pathlib import Path

from memecoin_ml.data import read


def operational(root, now=None):
    now = time.time_ns() if now is None else now
    c = read(root / "config.json")
    source = Path(c["source"])
    h = read(source / "health.json") if (source / "health.json").exists() else {}
    p = read(root / "paper-health.json") if (root / "paper-health.json").exists() else {}
    d = read(root / "health.json") if (root / "health.json").exists() else {}
    age = (now - d["at_ns"]) / 1e9 if "at_ns" in d else None
    source_age = (now - h["at_ns"]) / 1e9 if "at_ns" in h else None
    stopped = (root / "STOP").exists()
    source_stopped = (source / "STOP").exists()
    expired = now >= c["expires_ns"]
    runs = sorted((root / "training").glob("*"))
    last = read(runs[-1] / "reservation.json") if runs else None
    due = last["started_ns"] + 86400 * 10**9 if last else c["activation_ns"]
    train_stop = (root / "TRAIN_STOP").exists() or stopped
    training_state = (
        "stopped"
        if train_stop
        else "expired"
        if expired
        else "frozen_exam_no_training"
        if c.get("frozen_exam_models")
        else "budget_exhausted"
        if len(runs) >= c.get("training_max_runs", 3)
        else "waiting_daily_gate"
        if now < due
        else "awaiting_scheduler_and_maturity"
    )
    return dict(
        checked_ns=now,
        paper=dict(
            stopped=stopped,
            expired=expired,
            heartbeat_age_s=age,
            healthy=not stopped
            and not expired
            and age is not None
            and 0 <= age <= 120
            and p.get("status") not in ("failed", "recovery_cooldown"),
            supervisor_status=p.get("status", "unknown"),
            failure_category=p.get("category") if p.get("exit_code", 0) else None,
        ),
        source=dict(
            stopped=source_stopped,
            heartbeat_age_s=source_age,
            healthy=not source_stopped
            and not expired
            and source_age is not None
            and 0 <= source_age <= 180
            and now >= h.get("cooldown_ns", 0),
            attempts=h.get("attempts"),
            cooldown_ns=h.get("cooldown_ns", 0),
        ),
        training=dict(
            state=training_state,
            not_before_ns=due,
            reservations=len(runs),
            max_runs=c.get("training_max_runs", 3),
            required_new_matured=100,
            launch_interval_s=3600,
            automatic_promotion=False,
        ),
    )
