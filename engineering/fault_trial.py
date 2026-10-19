"""Bounded, explicit fault-injection driver and read-only pilot evidence auditor.

Never imported by the supervisor. Run only when fault injection is authorized:
python -m engineering.fault_trial --state PRIVATE --max-seconds 1200
python -m engineering.fault_trial --state PRIVATE --audit-only

The driver signals only same-UID process-group leaders whose recorded kernel
birth still matches. It never restarts workers, edits leases or manufactures
integration results. A separate supervisor performs ordinary recovery. Optional
--replace-chief admits one replacement episode only against fresh live quotas.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from .processes import process_identity, terminate_owned
from .supervisor import atomic, config


class TrialError(RuntimeError):
    pass


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def observe(state):
    """Read a consistent private SQLite snapshot; never opens project/data files."""
    path = state / "organization.sqlite"
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tasks = [
            dict(row)
            for row in db.execute(
                "SELECT id,team,state,epoch,active_attempt,candidate_sha FROM tasks ORDER BY id"
            )
        ]
        attempts = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY id")]
        for attempt in attempts:
            attempt["session"] = json.loads(attempt["session"]) if attempt["session"] else None
        role = (
            db.execute("SELECT body FROM executive_role WHERE role='CEO'").fetchone()
            if "executive_role" in tables
            else None
        )
        decisions = (
            [json.loads(row[0]) for row in db.execute("SELECT body FROM executive_decisions")]
            if "executive_decisions" in tables
            else []
        )
        events = [
            dict(row)
            for row in db.execute("SELECT seq,at,kind,entity,body FROM events ORDER BY seq")
        ]
        for event in events:
            event["body"] = json.loads(event["body"])
        return {
            "at": time.time(),
            "tasks": tasks,
            "attempts": attempts,
            "role": json.loads(role[0]) if role else None,
            "executive_decisions": decisions,
            "integrations": [dict(row) for row in db.execute("SELECT * FROM integrations")],
            "acceptances": [dict(row) for row in db.execute("SELECT * FROM acceptances")],
            "events": events,
        }
    finally:
        db.close()


def _operations(state):
    path = state / "operations.jsonl"
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            continue  # An in-progress append is not evidence.
    return result


def compact(view):
    return {
        "at": view["at"],
        "tasks": view["tasks"],
        "attempts": [
            {key: attempt[key] for key in ("id", "task_id", "epoch", "state", "lease_until")}
            | {
                "session": {
                    key: (attempt.get("session") or {}).get(key)
                    for key in ("model", "stage", "thread_id")
                }
            }
            for attempt in view["attempts"]
        ],
        "role": view["role"],
        "integration_count": len(view["integrations"]),
        "event_seq": max((event["seq"] for event in view["events"]), default=0),
    }


def _live(pid, birth):
    return type(pid) is int and pid > 1 and bool(birth) and process_identity(pid) == birth


def _live_attempts(view):
    tasks = {task["id"]: task for task in view["tasks"]}
    result = []
    for attempt in view["attempts"]:
        task = tasks[attempt["task_id"]]
        session = attempt.get("session") or {}
        identity = session.get("runner") or {}
        if (
            attempt["state"] == task["state"] == "running"
            and attempt["lease_until"] > time.time()
            and task["active_attempt"] == attempt["id"]
            and session.get("thread_id")
            and _live(identity.get("pid"), identity.get("birth"))
            and _live(identity.get("app_server_pid"), identity.get("app_server_birth"))
        ):
            result.append((task, attempt))
    return result


def _kill_identity(identity):
    # Stop the owner first so it cannot convert injected death into normal cleanup.
    result = {}
    for prefix, label in (("", "runner"), ("app_server_", "appserver")):
        pid, birth = identity.get(prefix + "pid"), identity.get(prefix + "birth")
        result[label] = {"pid": pid, "birth": birth, "killed": terminate_owned(pid, birth)}
    if not all(item["killed"] for item in result.values()):
        error = TrialError("one_or_more_owned_fault_targets_could_not_be_signalled")
        error.actions = result
        raise error
    return result


def _worker_target(receipt):
    attempt = receipt.get("attempt") or receipt.get("target") or {}
    return attempt if isinstance(attempt, dict) else {}


def audit(state, *, required_tasks=("EXP-001", "CTX-001", "PLAT-001")):
    """Check historical recorded faults against actual immutable state evidence.

    An audit reports missing conditions rather than inferring success from retries.
    It does not assert the authenticity of manually supplied historical receipts.
    """
    state = Path(state).resolve()
    view = observe(state)
    trial = _read(state / "fault-trial.json", {})
    chief = _read(state / "chief-fault.json", trial.get("chief_fault", {}))
    worker = _read(state / "worker-fault.json", trial.get("worker_fault", {}))
    supervisor = _read(state / "supervisor-fault.json", trial.get("supervisor_fault", {}))
    chief_record = chief.get("chief") or {}
    killed_epoch = chief_record.get("epoch")
    target = _worker_target(worker)
    faulted = next(
        (
            a
            for a in view["attempts"]
            if a["id"] == target.get("id")
            and a["epoch"] == target.get("epoch")
            and a["task_id"] == target.get("task_id")
        ),
        None,
    )
    replacement = next(
        (
            a
            for a in view["attempts"]
            if faulted and a["task_id"] == faulted["task_id"] and a["epoch"] > faulted["epoch"]
        ),
        None,
    )
    tasks = {task["id"]: task for task in view["tasks"]}
    integration_counts = {}
    for event in view["events"]:
        if event["kind"] == "candidate_integrated":
            integration_counts[event["entity"]] = integration_counts.get(event["entity"], 0) + 1
    chief_replaced = bool(
        killed_epoch
        and any(
            decision.get("created_epoch", 0) > killed_epoch
            and decision.get("id", "").startswith("executive-episode-")
            for decision in view["executive_decisions"]
        )
    )
    parallel = chief.get("attempts", [])
    initial_tasks = {task["id"]: task for task in chief.get("tasks", [])}
    teams = {
        initial_tasks[a["task_id"]]["team"]
        for a in parallel
        if (a.get("session") or {}).get("thread_id")
        and a["task_id"] in initial_tasks
        and a.get("state") == "running"
        and initial_tasks[a["task_id"]].get("state") == "running"
    }

    def killed(value, *, both=False):
        flags = value.get("killed", {})
        if isinstance(flags, bool):
            return flags and not both
        if both and not (
            {"runner", "appserver"} <= set(flags) or {"", "app_server_"} <= set(flags)
        ):
            return False
        return bool(flags) and all(
            item.get("killed") is True if isinstance(item, dict) else item is True
            for item in flags.values()
        )

    replacement_supervisor = supervisor.get("replacement") or {}
    relaunched = any(
        event.get("kind") == "supervisor_started"
        and event.get("pid") == replacement_supervisor.get("pid")
        and event.get("birth") == replacement_supervisor.get("birth")
        for event in _operations(state)
    )
    checks = {
        "two_distinct_team_sessions_recorded": len(teams) >= 2,
        "chief_fault_recorded": killed(chief, both=True) and bool(killed_epoch),
        "worker_fault_recorded": killed(worker, both=True)
        and faulted is not None
        and (target.get("session") or {}).get("stage") == "worker"
        and (target.get("session") or {}).get("model") == "gpt-6-luna",
        "faulted_worker_fence_replaced": replacement is not None,
        "supervisor_fault_recorded": killed(supervisor),
        "supervisor_relaunch_recorded": bool(replacement_supervisor) and relaunched,
        "executive_episode_replaced": chief_replaced,
        "required_tasks_integrated": all(
            tasks.get(name, {}).get("state") == "integrated" for name in required_tasks
        ),
        "unique_integrations": all(integration_counts.get(name) == 1 for name in required_tasks)
        and all(
            sum(row["task_id"] == name for row in view["integrations"]) == 1
            for name in required_tasks
        ),
    }
    return {
        "schema": "brain-fault-audit-v1",
        "observed_at": view["at"],
        "passed": all(checks.values()),
        "checks": checks,
        "faulted_attempt": target.get("id"),
        "replacement_attempt": replacement["id"] if replacement else None,
        "event_seq": max((event["seq"] for event in view["events"]), default=0),
        "limitation": (
            "Short local fault trial; no claim of days/weeks or physical-reboot recovery."
        ),
    }


def run_trial(state, *, max_seconds=1200, poll_seconds=1, replace_chief=False):
    """Execute one authorized sequence; refusal/failure is a durable non-passing result."""
    import fcntl

    state = Path(state).resolve()
    if not 1 <= max_seconds <= 3600 or not 0.1 <= poll_seconds <= 5:
        raise TrialError("invalid_trial_bounds")
    cfg = config(state)  # hash-checked frozen controller policy
    deadline = time.monotonic() + max_seconds
    path = state / "fault-trial.json"
    with (state / "fault-trial.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or any(
            (state / name).exists()
            for name in ("chief-fault.json", "worker-fault.json", "supervisor-fault.json")
        ):
            raise TrialError("existing_trial_requires_review_before_any_new_fault")
        receipt = {
            "schema": "brain-fault-trial-v1",
            "status": "running",
            "started_at": time.time(),
            "driver": {"pid": os.getpid(), "birth": process_identity(os.getpid())},
            "steps": [],
        }

        def save(step, **values):
            receipt["steps"].append({"step": step, "at": time.time(), **values})
            atomic(path, receipt)

        def wait(predicate, label):
            while time.monotonic() < deadline:
                if (state / "STOP").exists():
                    raise TrialError("deployment_stopped")
                view = observe(state)
                value = predicate(view)
                if value:
                    return view, value
                time.sleep(poll_seconds)
            raise TrialError("deadline_waiting_for_" + label)

        def spawn(command, name):
            with (state / (name + ".log")).open("ab") as output:
                process = subprocess.Popen(
                    [
                        cfg["python"],
                        "-m",
                        "engineering",
                        command,
                        "--state",
                        str(state),
                        *(["--max-seconds", "3600"] if command == "supervise" else []),
                    ],
                    cwd=cfg["controller"],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                )
            return {"pid": process.pid, "birth": process_identity(process.pid)}

        try:
            save("started")
            view, _ = wait(
                lambda v: len({task["team"] for task, _ in _live_attempts(v)}) >= 2,
                "parallel_team_sessions",
            )
            initial = compact(view)
            save("parallel_sessions_observed", snapshot=initial)
            view, chief = wait(
                lambda current: (
                    c
                    if len({task["team"] for task, _ in _live_attempts(current)}) >= 2
                    and (c := _read(state / "chief.json", {})).get("status") == "running"
                    and c.get("model") == "gpt-6-astra"
                    and c.get("reasoning") == "high"
                    and _live(c.get("pid"), c.get("birth"))
                    and _live(c.get("app_server_pid"), c.get("app_server_birth"))
                    else None
                ),
                "live_astra_episode",
            )
            receipt["chief_fault"] = {
                "chief": chief,
                "tasks": view["tasks"],
                "attempts": view["attempts"],
            }
            save("chief_fault_intent")
            receipt["chief_fault"]["killed"] = _kill_identity(chief)
            save("chief_fault_injected", snapshot=compact(observe(state)))
            view, selected = wait(
                lambda v: next(
                    (
                        (task, attempt)
                        for task, attempt in _live_attempts(v)
                        if task["id"] == "EXP-001" and attempt["session"].get("stage") == "worker"
                    ),
                    None,
                ),
                "experience_worker",
            )
            task, attempt = selected
            tools = sum(
                event.get("attempt_id") == attempt["id"]
                and event.get("event", {}).get("kind") == "tool"
                for event in _operations(state)
            )
            other = [(t, a) for t, a in _live_attempts(view) if t["team"] != task["team"]]
            if not other:
                raise TrialError("other_team_not_live_at_worker_fault")
            receipt["worker_fault"] = {
                "attempt": attempt,
                "other_team_snapshot": compact(view),
                "tools_observed": tools,
            }
            save("worker_fault_intent")
            receipt["worker_fault"]["killed"] = _kill_identity(attempt["session"]["runner"])
            save("worker_fault_injected")
            after = observe(state)
            valid_other = {
                t["id"]
                for t, _ in other
                if any(
                    current["id"] == t["id"]
                    and current["epoch"] == t["epoch"]
                    and current["state"]
                    in ("running", "submitted", "verified", "accepted", "integrated")
                    for current in after["tasks"]
                )
            }
            if not valid_other:
                raise TrialError("other_team_did_not_retain_its_fence")
            receipt["worker_fault"]["other_team_fence_survived"] = sorted(valid_other)
            save("other_team_fence_survived", task_ids=sorted(valid_other))
            supervisors = [
                event
                for event in _operations(state)
                if event.get("kind") == "supervisor_started"
                and _live(event.get("pid"), event.get("birth"))
            ]
            if len(supervisors) != 1:
                raise TrialError("cannot_prove_one_current_supervisor")
            old = {key: supervisors[0][key] for key in ("pid", "birth")}
            receipt["supervisor_fault"] = {"old": old}
            save("supervisor_fault_intent")
            if not terminate_owned(old["pid"], old["birth"]):
                raise TrialError("supervisor_signal_refused")
            receipt["supervisor_fault"]["killed"] = True
            receipt["supervisor_fault"]["replacement"] = spawn("supervise", "fault-supervisor")
            save("supervisor_relaunched")
            replacement_spawned = False
            while time.monotonic() < deadline:
                report = audit(state)
                if report["passed"]:
                    receipt.update(status="passed", audit=report, finished_at=time.time())
                    atomic(path, receipt)
                    return receipt
                if replace_chief and not replacement_spawned and _can_replace_chief(state, cfg):
                    receipt["chief_replacement"] = spawn("chief", "fault-chief-replacement")
                    replacement_spawned = True
                    save("chief_replacement_launched")
                if (state / "STOP").exists():
                    raise TrialError("deployment_stopped")
                time.sleep(poll_seconds)
            raise TrialError("recovery_acceptance_deadline")
        except Exception as exc:
            if getattr(exc, "actions", None) is not None:
                receipt["partial_signal_actions"] = exc.actions
            receipt.update(
                status="failed",
                finished_at=time.time(),
                error=type(exc).__name__ + ":" + str(exc)[:500],
            )
            atomic(path, receipt)
            return receipt


def _can_replace_chief(state, cfg):
    from .org_state import Store
    from .resources import choose_capacity
    from .supervisor import available_memory, measured_usage

    view = observe(state)
    role = view["role"]
    if role and role["status"] == "active" and role["lease_until"] > time.time():
        return False
    limits_path = state / "limits.json"
    if not limits_path.exists() or time.time() - limits_path.stat().st_mtime > 90:
        return False
    db = Store(state / "organization.sqlite")
    try:
        active = sum(task["state"] == "running" for task in view["tasks"])
        capacity = choose_capacity(
            _read(limits_path),
            measured_usage(state, db),
            {"available_memory_bytes": available_memory(), "cpu_count": os.cpu_count()},
            cfg["resources"],
            active_tokens=active * cfg["resources"]["per_worker_tokens"],
        )
        return capacity["slots"] > active
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=1200)
    parser.add_argument("--poll-seconds", type=float, default=1)
    parser.add_argument("--replace-chief", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    result = (
        audit(args.state)
        if args.audit_only
        else run_trial(
            args.state,
            max_seconds=args.max_seconds,
            poll_seconds=args.poll_seconds,
            replace_chief=args.replace_chief,
        )
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in result
                if key not in ("chief_fault", "worker_fault", "steps")
            },
            sort_keys=True,
        )
    )
    return 0 if result.get("passed") or result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
