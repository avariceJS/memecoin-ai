"""Durable bounded engineering supervisor; never imports Brain runtime code.

Supervisor and attempts are independent OS processes. SQLite is the authority;
Codex conversations are replaceable caches. Only this controller accepts and
integrates results. Agent-generated commands execute through Seatbelt.
"""

from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .codex_rpc import Codex
from .confinement import SandboxRunner, python_runtime_roots
from .git_gate import GitGate
from .metered_codex import metered_client
from .org_state import StaleAttempt, StateError, Store
from .processes import process_identity, terminate_owned
from .review_packet import build_review_packet
from .service import validate_deployment
from .verification_plan import (
    attempt_plan_matches,
    expand_bound_verifier_command,
    prepare_verification_plan,
)
from .verifier_result import validate_verifier_result

SOL = "gpt-6.1-sol"
LUNA = "gpt-6-luna"
LEASE = 45
LEAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "blocked"]},
        "worker_brief": {"type": "string"},
        "rationale": {"type": "string"},
        "contract_acks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "integer"},
                    "contract_hash": {"type": "string"},
                },
                "required": ["name", "version", "contract_hash"],
            },
        },
    },
    "required": ["decision", "worker_brief", "rationale", "contract_acks"],
}
WORKER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "tests", "limitations"],
}
REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "approved": {"type": "boolean"},
        "rationale": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["approved", "rationale", "findings"],
}


def _configured_contract_transitions(cfg, db):
    """Persist exact configured proposals and retain their current publication state."""
    contracts = cfg.get("contracts", {})
    if type(contracts) is not dict or any(type(name) is not str for name in contracts):
        raise StateError("contract configuration invalid")
    resolved = []
    for name, proposal in sorted(contracts.items()):
        if (
            not name.strip()
            or type(proposal) is not dict
            or type(proposal.get("version")) is not int
            or type(proposal.get("producer")) is not str
            or not proposal["producer"].strip()
            or (
                "producer_task_id" in proposal
                and (
                    type(proposal["producer_task_id"]) is not str
                    or not proposal["producer_task_id"].strip()
                )
            )
        ):
            raise StateError("contract configuration invalid")
        consumers = proposal.get("consumers", [proposal.get("consumer")])
        if (
            type(consumers) is not list
            or not consumers
            or any(type(team) is not str or not team.strip() for team in consumers)
        ):
            raise StateError("contract configuration invalid")
        current = db.get_contract(name)
        if current is None:
            raise StateError("contract transition source contract missing")
        transition = db.get_contract_transition(name, proposal["version"])
        already_active = current["version"] == proposal["version"] and current["body"] == proposal
        if transition is None and already_active:
            continue  # Exact legacy publication without transition evidence.
        if not already_active and current["version"] != proposal["version"] - 1:
            raise StateError("contract transition source version is stale")
        producer_task_id = proposal.get("producer_task_id")
        if transition is not None and producer_task_id is None:
            producer_task_id = transition["body"].get("producer_task_id")
        if producer_task_id is None and name == "schema-validation":
            # Preserve the original immutable deployment config during this migration.
            producer_task_id = "EXP-001"
        if type(producer_task_id) is not str or not producer_task_id.strip():
            raise StateError("contract producer task identity unavailable")
        transition = db.propose_contract_transition(
            name,
            proposal["version"] - 1,
            proposal["version"],
            proposal,
            producer_task_id=producer_task_id,
            producer_team=proposal["producer"],
            consumer_teams=consumers,
        )
        resolved.append(
            {
                "name": name,
                "version": proposal["version"],
                "body": transition["body"],
                "already_active": already_active,
            }
        )
    return resolved


def _validate_lead_contract_acks(value, *, team, transitions):
    """Accept only distinct, exact-hash acknowledgements for proposals this team owns."""
    if type(value) is not list or type(team) is not str:
        raise ValueError("lead_contract_ack_shape")
    allowed = {}
    for transition in transitions:
        body = transition["body"]
        if team in [body["producer_team"], *body["consumer_teams"]]:
            identity = (body["name"], body["to_version"], body["contract_hash"])
            allowed[identity] = {
                "name": identity[0],
                "version": identity[1],
                "contract_hash": identity[2],
            }
    accepted = []
    seen = set()
    for item in value:
        if type(item) is not dict or set(item) != {"name", "version", "contract_hash"}:
            raise ValueError("lead_contract_ack_shape")
        if (
            type(item["name"]) is not str
            or type(item["version"]) is not int
            or type(item["contract_hash"]) is not str
        ):
            raise ValueError("lead_contract_ack_shape")
        identity = (item["name"], item["version"], item["contract_hash"])
        if identity not in allowed or identity in seen:
            raise ValueError("lead_contract_ack_unbound")
        seen.add(identity)
        accepted.append(allowed[identity])
    return accepted


RULES = """You are an independent Brain engineering team session. The organization
controller, not any chief chat, owns your task and authority. Only the bounded
task below is authorized. Do not access market data, protected datasets, owner
home, credentials, runtime, services, network, collectors, training or publisher.
Use only org_exec for reading/editing/testing within this task. No other agent
delegation. Never change engineering/, AGENTS.md, .git, policies or test gates.
Output claims must identify evidence and limitations. This task overrides old
repo instructions about main: main must stay unchanged. Controller handles Git.
"""


def atomic(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temp.open("x") as f:
        json.dump(body, f, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    return json.loads(Path(path).read_text())


def _git_output(repo, *args, raw=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
    )
    p = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            *args,
        ],
        env=env,
        capture_output=True,
        text=not raw,
    )
    if p.returncode:
        stderr = p.stderr[-1500:]
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(stderr)
    return p.stdout


def git(repo, *args):
    return _git_output(repo, *args).strip()


def git_raw(repo, *args):
    """Return exact text output for NUL-delimited Git records without normalization."""
    output = _git_output(repo, *args, raw=True)
    try:
        return output.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeError("git_path_not_utf8") from None


def config(state):
    deployment = validate_deployment(state)
    expected_module = (Path(deployment["controller"]) / "engineering" / "supervisor.py").resolve()
    if Path(__file__).resolve() != expected_module:
        raise RuntimeError("supervisor_module_outside_frozen_controller")
    return deployment["config"]


def store(state):
    return Store(state / "organization.sqlite")


def log_event(state, kind, **values):
    # One append syscall per bounded record; regular private file, no agent access.
    record = json.dumps({"at": time.time(), "pid": os.getpid(), "kind": kind, **values}) + "\n"
    fd = os.open(state / "operations.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, record.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def runner(state, checkout, scratch, scopes=(), *, readonly=False):
    cfg = config(state)
    scratch.mkdir(parents=True, exist_ok=True)
    grants = []
    if not readonly:
        for name in scopes:
            parent = (checkout / name).parent
            if parent == checkout or checkout not in parent.parents:
                raise RuntimeError("worker_scope_requires_nonroot_parent")
            parent.mkdir(parents=True, exist_ok=True)
            grants.append(parent)
    return SandboxRunner(
        writable_roots=[scratch, *grants],
        read_roots=[checkout, *python_runtime_roots(cfg["python"]), "/opt/homebrew/opt/sqlite"],
        home=scratch,
        protected_roots=[state / "organization.sqlite", state / "config.json"],
    )


def tool_function(box, checkout, heartbeat):
    def execute(args):
        heartbeat()
        argv = args.get("argv")
        timeout = args.get("timeout")
        if type(timeout) is not int or not 1 <= timeout <= 90:
            raise ValueError("timeout must be integer seconds in 1..90; not milliseconds")
        if not isinstance(argv, list) or len(argv) > 100:
            raise ValueError("tool_arguments")
        result = box.run(argv, cwd=checkout, timeout=timeout, max_output_bytes=100000)
        heartbeat()
        out = dataclasses.asdict(result)
        out["stdout"], out["stderr"] = out["stdout"][-18000:], out["stderr"][-4000:]
        return out

    return execute


def structured(text):
    value = text.strip()
    if value.startswith("```"):
        value = "\n".join(value.splitlines()[1:-1])
    return json.loads(value)


def usage_tokens(result):
    return (
        result.get("usage_delta", {}).get("totalTokens")
        if result.get("usage_terminal") is True
        else None
    )


def reap_expired(state, db):
    expired = db.recover_expired()
    for attempt in db.list_attempts():
        if attempt["state"] != "expired":
            continue
        marker = state / "attempts" / str(attempt["id"]) / "reaped.json"
        if marker.exists():
            continue
        session = attempt.get("session") or {}
        identity = session.get("runner") or {}
        killed = {}
        for prefix in ("app_server_", ""):
            killed[prefix or "attempt"] = terminate_owned(
                identity.get(prefix + "pid"), identity.get(prefix + "birth")
            )
        process_file = marker.parent / "processes.json"
        if process_file.exists():
            for item in read(process_file):
                killed[str(item["pid"])] = terminate_owned(item["pid"], item["birth"])
        atomic(marker, {"at": time.time(), "attempt_id": attempt["id"], "killed": killed})
        log_event(state, "expired_processes_reconciled", attempt_id=attempt["id"], killed=killed)
    return expired


def run_attempt(state: Path, attempt_id: int):
    cfg, db = config(state), store(state)
    attempt = next(a for a in db.list_attempts() if a["id"] == attempt_id)
    task = db.get_task(attempt["task_id"])
    epoch = attempt["epoch"]
    # A queued OS child may start after its lease was already replaced.
    db.renew(attempt_id, epoch, LEASE)
    circuit = db.get_decision("circuit/controller-parse")
    if circuit is not None and circuit["body"].get("status") == "open":
        db.fail(attempt_id, epoch, "controller_circuit_open:structured_parse", retryable=False)
        db.close()
        return
    try:
        verification_plan = prepare_verification_plan(state, db, task, cfg)
    except ValueError as exc:
        reason = str(exc)
        log_event(
            state,
            "attempt_blocked",
            task=task["id"],
            attempt_id=attempt_id,
            epoch=epoch,
            reason=reason,
        )
        db.fail(attempt_id, epoch, "verifier_plan_unavailable:" + reason, retryable=False)
        db.close()
        return
    if not verification_plan.execution_qualified:
        log_event(
            state,
            "attempt_blocked",
            task=task["id"],
            attempt_id=attempt_id,
            epoch=epoch,
            reason=verification_plan.reason,
            plan_sha256=verification_plan.plan_sha256,
        )
        db.fail(
            attempt_id,
            epoch,
            "verifier_plan_unqualified:" + str(verification_plan.reason),
            retryable=False,
        )
        db.close()
        return
    run = state / "attempts" / str(attempt_id)
    run.mkdir(parents=True, exist_ok=True)
    checkout, scratch = run / "checkout", run / "scratch"
    native = run / "native"
    native.mkdir(exist_ok=True)
    processes = [{"pid": os.getpid(), "birth": process_identity(os.getpid())}]
    atomic(run / "processes.json", processes)

    def heartbeat():
        db.renew(attempt_id, epoch, LEASE)

    def sink(event):
        if event.get("kind") == "app_server_spawned":
            processes.append({"pid": event["pid"], "birth": process_identity(event["pid"])})
            atomic(run / "processes.json", processes)
        log_event(state, "codex", attempt_id=attempt_id, event=event)

    def controller_parse_fault(stage: str, exc: Exception):
        db.open_controller_parse_fault(
            attempt_id,
            epoch,
            stage=stage,
            exception_type=type(exc).__name__,
        )
        raise RuntimeError("controller_fault:structured_response") from exc

    stopped = threading.Event()

    def maintain():
        while not stopped.wait(5):
            try:
                heartbeat()
            except StaleAttempt:
                return

    threading.Thread(target=maintain, daemon=True).start()
    try:
        base = git(state / "integration", "rev-parse", "refs/heads/research/active")
        if not checkout.exists():
            git(
                state,
                "clone",
                "--no-hardlinks",
                "--no-local",
                str(state / "integration"),
                str(checkout),
            )
            git(checkout, "checkout", "--detach", base)
        box = runner(state, checkout, scratch, task["scope"])
        tool = tool_function(box, checkout, heartbeat)
        configured_transitions = _configured_contract_transitions(cfg, db)
        team_transitions = [
            item
            for item in configured_transitions
            if not item["already_active"]
            and task["team"] in [item["body"]["producer_team"], *item["body"]["consumer_teams"]]
        ]
        packet = {
            "task": task,
            "verification_plan": verification_plan.identity(),
            "effective_base": base,
            "python": cfg["python"],
            "contracts": {k: db.get_contract(k) for k in cfg["contracts"]},
            "proposed_contracts": [
                {
                    "name": item["name"],
                    "version": item["version"],
                    "contract_hash": item["body"]["contract_hash"],
                    "producer_task_id": item["body"]["producer_task_id"],
                    "producer_team": item["body"]["producer_team"],
                    "consumer_teams": item["body"]["consumer_teams"],
                    "body": item["body"]["body"],
                }
                for item in team_transitions
            ],
            "prior_attempts": [
                {"id": a["id"], "state": a["state"]} for a in db.list_attempts(task["id"])
            ],
            "prior_decision": db.get_decision("lead-" + task["id"]),
            "prior_failures": [
                e
                for e in db.events()
                if e["entity"] == task["id"]
                and e["kind"] in ("attempt_failed", "candidate_verified", "attempt_expired")
            ][-6:],
        }
        atomic(run / "packet.json", packet)
        readonly_box = runner(state, checkout, run / "lead-scratch", readonly=True)
        with metered_client(
            state,
            checkout,
            cfg=cfg,
            context={
                "role": "lead",
                "team": task["team"],
                "task": task["id"],
                "attempt": attempt_id,
                "authority": "CEO-20261020-02",
            },
            harness_cwd=native,
            event_sink=sink,
        ) as lead:
            thread = lead.start(SOL, RULES + "\nYou are the Sol lead of " + task["team"] + ".")
            identity = {
                "pid": os.getpid(),
                "birth": process_identity(os.getpid()),
                "app_server_pid": lead.proc.pid,
                "app_server_birth": process_identity(lead.proc.pid),
                "stage": "lead",
                "base": base,
                "checkout": str(checkout),
            }
            db.record_stage_session(attempt_id, epoch, thread, SOL, identity, stage="lead")
            out = lead.turn(
                thread,
                (
                    "Review the bounded task and relevant code if necessary. Decide its "
                    "contract and worker acceptance. Review each item in proposed_contracts. "
                    "If your team accepts a listed interface, return its exact name, version "
                    "and contract_hash in contract_acks; return an empty list otherwise. "
                    "You may acknowledge only proposals listing your team. Return JSON "
                    "{decision: approve|blocked, worker_brief: string, rationale: string, "
                    "contract_acks: [{name:string,version:integer,contract_hash:string}]}. "
                    "Do not implement.\n"
                )
                + json.dumps(packet),
                dispatch_id=f"attempt:{attempt_id}:lead",
                estimate={"tokens": 3500, "wpu": 1.0},
                model=SOL,
                tool=tool_function(readonly_box, checkout, heartbeat),
                heartbeat=heartbeat,
                deadline_seconds=300,
                output_schema=LEAD_SCHEMA,
            )
            atomic(run / "lead-raw.json", out)
            used_tokens = usage_tokens(out)
            db.record_usage(
                attempt_id,
                epoch,
                {**({"tokens": used_tokens} if used_tokens is not None else {}), "sol_turns": 1},
            )
            if used_tokens is None:
                log_event(
                    state,
                    "usage_unknown",
                    attempt_id=attempt_id,
                    dispatch_id=f"attempt:{attempt_id}:lead",
                )
            try:
                plan = structured(out["text"])
                if (
                    not isinstance(plan, dict)
                    or set(plan) != {"decision", "worker_brief", "rationale", "contract_acks"}
                    or plan["decision"] not in ("approve", "blocked")
                    or not isinstance(plan["worker_brief"], str)
                    or not isinstance(plan["rationale"], str)
                ):
                    raise ValueError("lead_result_shape")
                plan["contract_acks"] = _validate_lead_contract_acks(
                    plan["contract_acks"], team=task["team"], transitions=team_transitions
                )
            except Exception as exc:
                lead.record_outcome(
                    f"attempt:{attempt_id}:lead", "failed", {"parser_error": type(exc).__name__}
                )
                controller_parse_fault("lead", exc)
            atomic(run / "lead.json", {**out, "plan": plan, "thread": thread})
            db.put_decision(
                "lead-" + task["id"],
                {"team": task["team"], "thread": thread, "model": SOL, "base": base, "plan": plan},
            )
            if plan.get("decision") != "approve":
                raise RuntimeError("lead_blocked")
            for ack in plan["contract_acks"]:
                db.acknowledge_contract_transition(
                    ack["name"], ack["version"], task["team"], ack["contract_hash"]
                )
        with metered_client(
            state,
            checkout,
            cfg=cfg,
            context={
                "role": "worker",
                "team": task["team"],
                "task": task["id"],
                "attempt": attempt_id,
                "authority": "CEO-20261020-02",
            },
            harness_cwd=native,
            event_sink=sink,
        ) as worker:
            thread = worker.start(
                LUNA, RULES + "\nYou are the Luna implementation worker of " + task["team"] + "."
            )
            identity.update(
                stage="worker",
                app_server_pid=worker.proc.pid,
                app_server_birth=process_identity(worker.proc.pid),
            )
            db.record_stage_session(attempt_id, epoch, thread, LUNA, identity, stage="worker")
            prompt = (
                "Implement the task, including meaningful regression tests. Run only the "
                "focused commands in acceptance. "
                "Do not commit; controller captures the patch. You can edit with Python "
                "through org_exec. "
                "Return a concise JSON object with summary, tests and limitations.\n"
                + json.dumps(packet)
                + "\nSol decision:\n"
                + json.dumps(plan)
            )
            out = worker.turn(
                thread,
                prompt,
                dispatch_id=f"attempt:{attempt_id}:worker",
                estimate={
                    "tokens": min(cfg.get("resources", {}).get("per_worker_tokens", 4000), 4000),
                    "wpu": 1.0,
                },
                model=LUNA,
                tool=tool,
                heartbeat=heartbeat,
                deadline_seconds=600,
                output_schema=WORKER_SCHEMA,
            )
            atomic(run / "worker-raw.json", out)
            used_tokens = usage_tokens(out)
            db.record_usage(
                attempt_id,
                epoch,
                {**({"tokens": used_tokens} if used_tokens is not None else {}), "luna_turns": 1},
            )
            if used_tokens is None:
                log_event(
                    state,
                    "usage_unknown",
                    attempt_id=attempt_id,
                    dispatch_id=f"attempt:{attempt_id}:worker",
                )
            try:
                worker_result = structured(out["text"])
                if (
                    not isinstance(worker_result, dict)
                    or set(worker_result) != {"summary", "tests", "limitations"}
                    or not isinstance(worker_result["summary"], str)
                    or not isinstance(worker_result["tests"], list)
                    or any(not isinstance(value, str) for value in worker_result["tests"])
                    or not isinstance(worker_result["limitations"], list)
                    or any(not isinstance(value, str) for value in worker_result["limitations"])
                ):
                    raise ValueError("worker_result_shape")
            except Exception as exc:
                worker.record_outcome(
                    f"attempt:{attempt_id}:worker", "failed", {"parser_error": type(exc).__name__}
                )
                controller_parse_fault("worker", exc)
            atomic(run / "worker.json", {**out, "thread": thread})
        heartbeat()
        changed = git(checkout, "status", "--porcelain", "--untracked-files=all")
        if not changed:
            raise RuntimeError("no_implementation_change")
        # Immutable task scope controls staging. .git was never writable in sandbox.
        for name in task["scope"]:
            if (checkout / name).is_symlink():
                raise RuntimeError("symlink_submission_rejected")
        git(checkout, "add", "--", *task["scope"])
        git(
            checkout,
            "-c",
            "user.name=Brain Organization",
            "-c",
            "user.email=brain-org@localhost",
            "commit",
            "-m",
            task["id"] + ": " + task["title"],
        )
        sha = git(checkout, "rev-parse", "HEAD")
        db.submit(
            attempt_id,
            epoch,
            sha,
            {
                "base": base,
                "checkout": str(checkout),
                "run": str(run),
                "worker_thread": thread,
                "verification_plan": verification_plan.identity(),
            },
        )
        log_event(state, "submitted", task=task["id"], sha=sha, attempt_id=attempt_id)
    except Exception as exc:
        log_event(
            state,
            "attempt_failed",
            task=task["id"],
            attempt_id=attempt_id,
            error=type(exc).__name__ + ":" + str(exc)[:1200],
        )
        try:
            db.fail(
                attempt_id,
                epoch,
                type(exc).__name__ + ":" + str(exc)[:500],
                retryable=("lead_blocked" not in str(exc) and "controller_fault:" not in str(exc)),
            )
        except StaleAttempt:
            pass
    finally:
        stopped.set()
        db.close()


def resolve_controller_parse_fault(
    state: Path,
    *,
    recovery_id: str,
    fix_sha256: str,
    review_ref: str,
    authorization_ref: str,
):
    """Close the parser circuit only with pinned fix, review, and recovery authority."""
    values = (recovery_id, review_ref, authorization_ref)
    if any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in values):
        raise ValueError("controller_recovery_evidence_required")
    if (
        not isinstance(fix_sha256, str)
        or len(fix_sha256) != 64
        or any(char not in "0123456789abcdef" for char in fix_sha256)
    ):
        raise ValueError("controller_recovery_fix_hash_required")
    db = store(state)
    try:
        circuit = db.get_decision("circuit/controller-parse")
        obligation = db.get_decision("recovery-obligation/controller-parse")
        if (
            circuit is None
            or circuit["body"].get("status") != "open"
            or obligation is None
            or obligation["body"].get("status") != "open"
        ):
            raise ValueError("controller_recovery_obligation_missing")
        db.resolve_controller_parse_fault(
            expected_circuit_version=circuit["version"],
            expected_obligation_version=obligation["version"],
            recovery_id=recovery_id,
            fix_sha256=fix_sha256,
            review_ref=review_ref,
            authorization_ref=authorization_ref,
        )
    finally:
        db.close()


def verify_one(state, task):
    cfg, db = config(state), store(state)
    data = db.get_candidate(task["id"])
    evidence = data["evidence"]
    python = cfg["python"]
    try:
        plan = prepare_verification_plan(state, db, task, cfg)
    except ValueError as exc:
        log_event(
            state,
            "verification_blocked",
            task=task["id"],
            candidate=task["candidate_sha"],
            reason=str(exc),
        )
        db.close()
        return False
    if not plan.execution_qualified:
        log_event(
            state,
            "verification_blocked",
            task=task["id"],
            candidate=task["candidate_sha"],
            reason=plan.reason,
            plan_sha256=plan.plan_sha256,
        )
        db.close()
        return False
    matches, reason = attempt_plan_matches(plan, evidence)
    if not matches:
        log_event(
            state,
            "verification_blocked",
            task=task["id"],
            candidate=task["candidate_sha"],
            reason=reason,
            plan_sha256=plan.plan_sha256,
        )
        db.close()
        return False
    policy = plan.policy
    gate = GitGate(state / "integration", state / "integration", policy, state / "receipts")
    try:
        candidate = gate.submit(
            Path(evidence["checkout"]), task["candidate_sha"], evidence["base"], task["scope"]
        )
    except Exception as exc:
        db.verify(task["id"], task["candidate_sha"], False, {"error": str(exc)[:2000]})
        db.close()
        return True
    log_event(state, "verification_started", task=task["id"], sha=candidate.sha)
    verify_root = state / "verification" / candidate.sha
    verify_root.mkdir(parents=True, exist_ok=True)

    def verification(checkout, sha, fixed_policy):
        scratch = verify_root / "scratch"
        box = runner(state, checkout, scratch, readonly=True)
        # Read trusted test harness; the candidate cannot alter these acceptance checks.
        box = SandboxRunner(
            writable_roots=[scratch],
            home=scratch,
            read_roots=[
                checkout,
                state / "trusted",
                *python_runtime_roots(python),
                "/opt/homebrew/opt/sqlite",
            ],
            protected_roots=[state / "organization.sqlite", state / "config.json"],
        )
        results = []
        if plan.binding_sha256 is None:
            commands = [(command, None, None) for command in fixed_policy.commands]
        else:
            flattened = tuple(
                command for verifier in plan.bound_verifiers for command in verifier.commands
            )
            if not plan.bound_verifiers or flattened != fixed_policy.commands:
                raise RuntimeError("bound_verifier_plan_commands_mismatch")
            commands = [
                (command, verifier, command_index)
                for verifier in plan.bound_verifiers
                for command_index, command in enumerate(verifier.commands)
            ]
        for command_index, (command, verifier, verifier_command_index) in enumerate(commands):
            if verifier is None:
                argv = [str(checkout) if arg == "{checkout}" else arg for arg in command]
            else:
                argv = expand_bound_verifier_command(
                    command,
                    checkout=checkout,
                    task_id=task["id"],
                    harness_path=verifier.harness_path,
                    interpreter_path=verifier.interpreter_path,
                )
            result = box.run(
                argv,
                cwd=checkout,
                timeout=90,
                max_output_bytes=200000,
                env_overrides=dict(fixed_policy.env),
                expected_executable_sha256=(
                    verifier.interpreter_sha256
                    if verifier is not None and verifier_command_index == 0
                    else None
                ),
                expected_executable_cdhash=(
                    verifier.interpreter_runtime_cdhash
                    if verifier is not None and verifier_command_index == 0
                    else None
                ),
                expected_executable_main_path=(
                    verifier.interpreter_runtime_path
                    if verifier is not None and verifier_command_index == 0
                    else None
                ),
            )
            results.append(dataclasses.asdict(result))
            atomic(verify_root / "commands.json", results)
            if result.returncode or result.timed_out or result.output_truncated:
                raise RuntimeError("verification_command_failed:" + json.dumps(results[-1])[-2500:])
            if verifier is None and command_index == 0:
                proof = json.loads(result.stdout)
                if (
                    proof.get("schema") != "org-pilot-acceptance-v1"
                    or proof.get("case") != task["id"]
                    or proof.get("passed") is not True
                ):
                    raise RuntimeError("missing_independent_acceptance_completion")
            elif verifier is not None and verifier_command_index == 0:
                validate_verifier_result(
                    result.stdout,
                    {
                        "acceptance_id": verifier.acceptance_id,
                        "result_schema": verifier.result_schema,
                        "case_field": verifier.case_field,
                        "passed_field": verifier.passed_field,
                    },
                )
        diff = git(
            state / "integration",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            candidate.integration_tip,
            sha,
        )
        merged_path_output = git_raw(
            state / "integration",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--name-only",
            "-z",
            candidate.integration_tip,
            sha,
        )
        path_fields = merged_path_output.split("\0")
        if not path_fields or path_fields[-1] != "":
            raise RuntimeError("invalid_merged_path_output")
        merged_changed_paths = tuple(path_fields[:-1])
        review_packet = build_review_packet(
            task, candidate, fixed_policy, results, diff, merged_changed_paths
        )
        atomic(verify_root / "review-packet.json", review_packet)
        native = verify_root / "native"
        native.mkdir(exist_ok=True)
        with metered_client(
            state,
            checkout,
            cfg=cfg,
            context={
                "role": "verifier",
                "team": task["team"],
                "task": task["id"],
                "attempt": data["attempt_id"],
                "candidate": sha,
                "authority": "CEO-20261020-02",
            },
            harness_cwd=native,
            event_sink=lambda event: log_event(state, "codex", role="verifier", event=event),
        ) as reviewer:
            thread = reviewer.start(
                SOL,
                RULES
                + "\nYou are the independent verification reviewer. You did not author this patch. "
                "No editing.",
            )
            review = reviewer.turn(
                thread,
                (
                    "Review this exact candidate for correctness, scope, regression risk "
                    "and scientific semantics. Fixed independent acceptance and focused "
                    "tests passed. Return JSON "
                    "{approved: boolean, findings: [strings], rationale: string}. Block "
                    "concrete defects, "
                    "not hypothetical unrelated improvements.\n"
                )
                + json.dumps(review_packet, sort_keys=True, separators=(",", ":")),
                dispatch_id=f"verify:{task['id']}:{sha}",
                estimate={"tokens": 3500, "wpu": 1.0},
                model=SOL,
                tool=lambda _: {"error": "review_tools_disabled"},
                deadline_seconds=240,
                output_schema=REVIEW_SCHEMA,
            )
            atomic(verify_root / "review-raw.json", review)
            try:
                decision = structured(review["text"])
            except Exception as exc:
                reviewer.record_outcome(
                    f"verify:{task['id']}:{sha}", "failed", {"parser_error": type(exc).__name__}
                )
                raise
            atomic(
                verify_root / "review.json",
                {**review, "decision": decision, "thread": thread, "model": SOL},
            )
            if decision.get("approved") is not True:
                raise RuntimeError("independent_review_rejected:" + json.dumps(decision))
        return {
            "status": "passed",
            "commands_sha256": hashlib.sha256(
                (verify_root / "commands.json").read_bytes()
            ).hexdigest(),
            "review_sha256": hashlib.sha256((verify_root / "review.json").read_bytes()).hexdigest(),
            "review_packet_sha256": review_packet["packet_sha256"],
            "review_thread": thread,
        }

    try:
        receipt = gate.verify(candidate, verification)
        # Persist reconciliation intent BEFORE ref update. Recovery checks candidate ancestry.
        intent = {
            "task": task["id"],
            "submitted_sha": task["candidate_sha"],
            "attempt_id": data["attempt_id"],
            "epoch": data["epoch"],
            "candidate_sha": candidate.sha,
            "expected_tip": candidate.integration_tip,
            "receipt": receipt.digest,
        }
        atomic(state / "intents" / (task["id"] + ".json"), intent)
        db.verify(task["id"], task["candidate_sha"], True, intent)
        db.accept(task["id"], task["candidate_sha"])
        gate.publish(candidate, receipt.digest, candidate.integration_tip)
        db.integrate(task["id"], task["candidate_sha"], candidate.sha, intent)
        log_event(state, "integrated", **intent)
        activate_contract(state, db)
    except Exception as exc:
        log_event(state, "verification_failed", task=task["id"], error=str(exc)[-3000:])
        if db.get_task(task["id"])["state"] == "submitted":
            db.verify(task["id"], task["candidate_sha"], False, {"error": str(exc)[-3000:]})
    finally:
        db.close()
    return True


def reconcile(state, db):
    for path in (state / "intents").glob("*.json"):
        intent = read(path)
        task = db.get_task(intent["task"])
        if task["candidate_sha"] != intent["submitted_sha"]:
            log_event(state, "reconciliation_blocked", task=intent["task"], reason="stale_intent")
            continue
        plan = None
        if task["state"] in ("submitted", "verified", "accepted"):
            try:
                plan = prepare_verification_plan(state, db, task, config(state))
            except ValueError as exc:
                log_event(
                    state,
                    "reconciliation_blocked",
                    task=task["id"],
                    reason=str(exc),
                )
                continue
            if not plan.execution_qualified:
                log_event(
                    state,
                    "reconciliation_blocked",
                    task=task["id"],
                    reason=plan.reason,
                    plan_sha256=plan.plan_sha256,
                )
                continue
            candidate = db.get_candidate(task["id"])
            matches, reason = attempt_plan_matches(plan, candidate["evidence"])
            if not matches:
                log_event(
                    state,
                    "reconciliation_blocked",
                    task=task["id"],
                    reason=reason,
                    plan_sha256=plan.plan_sha256,
                )
                continue
        if task["state"] == "submitted":
            gate = GitGate(
                state / "integration",
                state / "integration",
                plan.policy,
                state / "receipts",
            )
            gate._require_receipt(intent["receipt"], intent["candidate_sha"])
            db.verify(task["id"], task["candidate_sha"], True, intent)
            task = db.get_task(task["id"])
        if task["state"] == "verified":
            db.accept(task["id"], intent["submitted_sha"])
            task = db.get_task(task["id"])
        if task["state"] == "accepted":
            tip = git(state / "integration", "rev-parse", "refs/heads/research/active")
            if tip == intent["candidate_sha"]:
                db.integrate(task["id"], intent["submitted_sha"], tip, intent)
            elif tip == intent["expected_tip"]:
                # Receipt was recorded in trusted intent and protected store before acceptance.
                gate = GitGate(
                    state / "integration",
                    state / "integration",
                    plan.policy,
                    state / "receipts",
                )
                gate._require_receipt(intent["receipt"], intent["candidate_sha"])
                git(
                    state / "integration",
                    "update-ref",
                    "refs/heads/research/active",
                    intent["candidate_sha"],
                    tip,
                )
                db.integrate(task["id"], intent["submitted_sha"], intent["candidate_sha"], intent)
            else:
                raise RuntimeError("integration_intent_requires_reconciliation")
    activate_contract(state, db)


def activate_contract(state, db):
    """Reconcile configured contract transitions without coupling names or versions."""
    transitions = _configured_contract_transitions(config(state), db)
    for transition in transitions:
        try:
            activated = db.activate_contract_transition(transition["name"], transition["version"])
        except StateError as exc:
            if str(exc) in {
                "contract producer is not integrated",
                "contract transition acknowledgements incomplete",
            }:
                continue
            raise
        if activated["created"]:
            log_event(
                state,
                "contract_activated",
                name=transition["name"],
                version=transition["version"],
                agreement_hash=activated["body"]["contract_hash"],
            )


def supervise(state: Path, max_seconds=1800):
    from .resources import choose_capacity

    state = state.resolve()
    cfg, db = config(state), store(state)
    with (state / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log_event(state, "supervisor_started", birth=process_identity(os.getpid()))
        end = time.monotonic() + max_seconds
        native = state / "budget-native"
        native.mkdir(exist_ok=True)
        blocked_verification = set()
        with Codex(native, codex=cfg.get("codex_executable", "codex")) as account:
            last_read, limits = None, None
            while time.monotonic() < end and not (state / "STOP").exists():
                reconcile(state, db)
                reap_expired(state, db)
                tasks = db.list_tasks()
                # Each verifier is serial and may block dispatch briefly; active workers
                # maintain their own leases independently and continue during verification.
                submitted = next(
                    (
                        t
                        for t in tasks
                        if t["state"] == "submitted" and t["id"] not in blocked_verification
                    ),
                    None,
                )
                if submitted:
                    if not verify_one(state, submitted):
                        blocked_verification.add(submitted["id"])
                    continue
                if last_read is None or time.monotonic() - last_read > 60:
                    try:
                        limits = account.call("account/rateLimits/read")
                        quota_observed_at = time.time()
                    except Exception:
                        limits = {"ordinaryUsageAllowed": None}
                        quota_observed_at = None
                    # Do not persist identity or reset-credit objects.
                    limits = {
                        k: limits.get(k)
                        for k in ("ordinaryUsageAllowed", "rateLimits", "rateLimitsByLimitId")
                    }
                    limits["observed_at"] = quota_observed_at
                    atomic(state / "limits.json", limits)
                    last_read = time.monotonic()
                active = [t for t in db.list_tasks() if t["state"] == "running"]
                ready = [
                    t for t in db.ready_tasks() if t["team"] not in {a["team"] for a in active}
                ]
                decision = choose_capacity(
                    limits,
                    measured_usage(state, db),
                    {
                        "available_memory_bytes": available_memory(),
                        "cpu_count": os.cpu_count(),
                        "active_team_ids": sorted({t["team"] for t in active}),
                    },
                    cfg["resources"],
                    active_tokens=len(active) * cfg["resources"]["per_worker_tokens"],
                    ready_tasks=ready,
                    active_scopes=[s for t in active for s in t["scope"]],
                )
                if not active and not ready:
                    from .planning import plan_next

                    if decision["slots"] and plan_next(state, db):
                        continue
                    snapshot = db.quiescence_snapshot()
                    readiness = snapshot["readiness"]
                    if any(
                        row["state"] == "running"
                        or (
                            row["state"] == "submitted"
                            and row["task_id"] not in blocked_verification
                        )
                        for row in readiness
                    ):
                        # The earlier active/ready reads may have gone stale while the
                        # supervisor reconciled and planned. Let the next pass act on the
                        # fresh state instead of recording a quiescence handoff for it.
                        time.sleep(2)
                        continue
                    decision = choose_capacity(
                        limits,
                        measured_usage(state, db),
                        {
                            "available_memory_bytes": available_memory(),
                            "cpu_count": os.cpu_count(),
                            "active_team_ids": [],
                        },
                        cfg["resources"],
                        active_tokens=0,
                    )
                    if decision["slots"] and any(row["eligible"] for row in readiness):
                        # Capacity and task readiness were observed separately; avoid
                        # classifying ready work as dependency-blocked after capacity changed.
                        time.sleep(2)
                        continue
                    resource_reason = decision.get("reason", "unknown")
                    if decision["slots"] == 0:
                        reason = "quota-unavailable"
                    elif snapshot["contract_reviews"]["pending_count"]:
                        reason = "contract-reviews-pending"
                    elif any(row["state"] == "pending" for row in readiness):
                        reason = "blocked-dependencies"
                    else:
                        reason = "catalogue-exhausted"
                    fingerprint_data = {
                        "snapshot": snapshot,
                        "resource_reason": resource_reason,
                        "reason": reason,
                    }
                    fingerprint = hashlib.sha256(
                        json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    recorded = db.record_quiescence_handoff_if_current(
                        fingerprint,
                        snapshot,
                        {
                            "owner": "Sol",
                            "status": "requested",
                            "reason": reason,
                            "resource_reason": resource_reason,
                            "fingerprint": fingerprint,
                            "review_requests": snapshot["review_requests"],
                        },
                    )
                    if not recorded:
                        time.sleep(2)
                        continue
                    break
                free = decision["slots"]
                for task in decision["selected_tasks"][:free]:
                    attempt = db.claim(task["id"], LUNA, LEASE)
                    if not attempt:
                        continue
                    log = (state / "attempt-output.log").open("ab")
                    p = subprocess.Popen(
                        [
                            cfg["python"],
                            "-m",
                            "engineering",
                            "attempt",
                            "--state",
                            str(state),
                            "--attempt",
                            str(attempt["id"]),
                        ],
                        cwd=cfg["controller"],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                    log.close()
                    log_event(
                        state,
                        "attempt_spawned",
                        attempt_id=attempt["id"],
                        task=task["id"],
                        child_pid=p.pid,
                    )
                atomic(state / "status.json", status(state, db, decision=decision))
                render_desk(state, db, decision)
                time.sleep(2)
        atomic(state / "status.json", status(state, db))
        render_desk(state, db)
        log_event(state, "supervisor_stopped")
    db.close()


def available_memory():
    p = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True)
    import re

    size = re.search(r"page size of (\d+)", p.stdout)
    pages = re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", p.stdout)
    return int(size[1]) * sum(map(int, pages)) if size and pages else 0


def status(state, db=None, **extra):
    owned = db is None
    db = db or store(state)
    result = {
        "schema": "brain-engineering-status-v1",
        "observed_at": time.time(),
        "tasks": db.list_tasks(),
        "attempts": db.list_attempts(),
        "usage": measured_usage(state, db),
        "integration_sha": git(state / "integration", "rev-parse", "refs/heads/research/active"),
        "remote_publication_enabled": False,
        **extra,
    }
    if owned:
        db.close()
    return result


def render_desk(state, db=None, resource_decision=None):
    from .executive import Executive

    owned = db is None
    db = db or store(state)
    view = Executive(db).snapshot(history_limit=3, open_limit=10)
    lines = [
        "# Founder Desk — Brain Engineering Organization",
        "",
        "Ответственный: Astra High. Это проекция устойчивого журнала, не источник полномочий.",
        "",
        "## Последнее решение Astra",
        "",
    ]
    for decision in view["decisions"][:1]:
        lines += [decision["summary"], ""]
    if not view["decisions"]:
        lines += ["Первый управленческий эпизод ещё не завершён.", ""]
    lines += ["## Проверенная работа", "", "| Задача | Отдел | Состояние |", "|---|---|---|"]
    lines += [f"| {t['id']} | {t['team']} | {t['state']} |" for t in view["tasks"]]
    lines += [
        "",
        "Интеграция локальная; удалённая публикация требует отдельной квитанции.",
        "Стабильная main защищена отдельной процедурой выпуска.",
        "",
        "## Обязательства и запросы",
        "",
    ]
    lines += [f"- {o['title']} — {o['status']} ({o['owner_role']})." for o in view["obligations"]]
    lines += [f"- Запрос: {r['requested_change']}" for r in view["requests"]]
    if not view["requests"]:
        lines += ["Ресурсов или действий основателя сейчас не запрошено."]
    lines += [
        "",
        "## Измерения и границы",
        "",
        f"Учтённые токены: {measured_usage(state, db)['total'].get('tokens', 'unknown')}.",
        "Непрерывная многодневная работа и восстановление после физической перезагрузки "
        "ещё не квалифицированы. Сбор рынка, обучение и торговля выключены.",
    ]
    if resource_decision:
        lines += [
            "Решение ресурсов: `" + json.dumps(resource_decision.get("reason", "unknown")) + "`."
        ]
    lines += [
        "",
        "Полная история: organization.sqlite; квитанции: receipts/; "
        "компактные результаты проверок: verification/.",
        "",
    ]
    path = state / "OWNER-DESK.md"
    temp = path.with_suffix(".md.tmp")
    with temp.open("w") as f:
        f.write("\n".join(lines))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    if owned:
        db.close()
    return path


def measured_usage(state, db):
    # organization.sqlite remains historical evidence. Current usage and active
    # obligations come only from the sidecar ledger when it already exists.
    result = db.usage_summary()
    ledger_path = state / "usage-ledger.sqlite"
    if ledger_path.exists():
        from .usage_ledger import UsageLedger

        ledger = UsageLedger(ledger_path)
        try:
            projection = ledger.summary()
        finally:
            ledger.close()
        result["current_usage"] = projection["incremental"]
        result["reservations"] = projection["reservations"]
        result["usage_unresolved"] = projection["unresolved"]
        result["current_by_model"] = projection["by_model"]
    else:
        result["current_usage"] = None
        result["reservations"] = []
        result["usage_unresolved"] = [{"reason": "ledger_missing"}]
        result["current_by_model"] = {}
    return result
