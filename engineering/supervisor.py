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
from .git_gate import GitGate, VerificationPolicy
from .metered_codex import metered_client
from .org_state import StaleAttempt, Store
from .processes import process_identity, terminate_owned
from .service import validate_deployment

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
        "contract_ack": {"type": "boolean"},
    },
    "required": ["decision", "worker_brief", "rationale", "contract_ack"],
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


def git(repo, *args):
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
        text=True,
    )
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return p.stdout.strip()


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
        packet = {
            "task": task,
            "effective_base": base,
            "python": cfg["python"],
            "contracts": {k: db.get_contract(k) for k in cfg["contracts"]},
            "proposed_contracts": cfg["contracts"],
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
                    "contract and worker acceptance. If a proposed interface affects your "
                    "team, explicitly assess compatibility. Return JSON "
                    "{decision: approve|blocked, worker_brief: string, rationale: string, "
                    "contract_ack: boolean}. Do not implement.\n"
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
                if not isinstance(plan, dict) or not all(
                    key in plan
                    for key in ("decision", "worker_brief", "rationale", "contract_ack")
                ):
                    raise ValueError("lead_result_shape")
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
            if plan.get("contract_ack") is True:
                proposal = cfg["contracts"]["schema-validation"]
                digest = hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()
                key = "contract-ack/" + digest + "/" + task["team"]
                if db.get_decision(key) is None:
                    db.put_decision(
                        key,
                        {
                            "team": task["team"],
                            "thread": thread,
                            "model": SOL,
                            "contract_hash": digest,
                            "version": 2,
                            "contract_ack": True,
                        },
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
                if not isinstance(worker_result, dict) or not all(
                    key in worker_result for key in ("summary", "tests", "limitations")
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
            {"base": base, "checkout": str(checkout), "run": str(run), "worker_thread": thread},
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
    commands = [(python, str(state / "trusted/pilot_acceptance.py"), task["id"], "{checkout}")]
    commands += [tuple(x) for x in cfg["tasks"][task["id"]]["checks"]]
    policy = VerificationPolicy(tuple(commands), policy_id="brain-org-pilot-v1:" + task["id"])
    gate = GitGate(state / "integration", state / "integration", policy, state / "receipts")
    try:
        candidate = gate.submit(
            Path(evidence["checkout"]), task["candidate_sha"], evidence["base"], task["scope"]
        )
    except Exception as exc:
        db.verify(task["id"], task["candidate_sha"], False, {"error": str(exc)[:2000]})
        db.close()
        return
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
        for command in fixed_policy.commands:
            argv = [str(checkout) if a == "{checkout}" else a for a in command]
            result = box.run(argv, cwd=checkout, timeout=90, max_output_bytes=200000)
            results.append(dataclasses.asdict(result))
            atomic(verify_root / "commands.json", results)
            if result.returncode or result.timed_out or result.output_truncated:
                raise RuntimeError("verification_command_failed:" + json.dumps(results[-1])[-2500:])
            if command == fixed_policy.commands[0]:
                proof = json.loads(result.stdout)
                if (
                    proof.get("schema") != "org-pilot-acceptance-v1"
                    or proof.get("case") != task["id"]
                    or proof.get("passed") is not True
                ):
                    raise RuntimeError("missing_independent_acceptance_completion")
        diff = git(
            state / "integration",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            candidate.integration_tip,
            sha,
        )
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
                + json.dumps(
                    {
                        "task": task,
                        "sha": sha,
                        "diff": diff[:50000],
                        "checks": [
                            {
                                "argv": r["argv"],
                                "returncode": r["returncode"],
                                "stdout": r["stdout"][-2000:],
                            }
                            for r in results
                        ],
                    }
                ),
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


def reconcile(state, db):
    for path in (state / "intents").glob("*.json"):
        intent = read(path)
        task = db.get_task(intent["task"])
        if task["candidate_sha"] != intent["submitted_sha"]:
            raise RuntimeError("stale_integration_intent")
        if task["state"] == "submitted":
            policy_commands = [
                (
                    config(state)["python"],
                    str(state / "trusted/pilot_acceptance.py"),
                    task["id"],
                    "{checkout}",
                )
            ]
            policy_commands += [tuple(x) for x in config(state)["tasks"][task["id"]]["checks"]]
            gate = GitGate(
                state / "integration",
                state / "integration",
                VerificationPolicy(
                    tuple(policy_commands), policy_id="brain-org-pilot-v1:" + task["id"]
                ),
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
                policy_commands = [
                    (
                        config(state)["python"],
                        str(state / "trusted/pilot_acceptance.py"),
                        task["id"],
                        "{checkout}",
                    )
                ]
                policy_commands += [tuple(x) for x in config(state)["tasks"][task["id"]]["checks"]]
                gate = GitGate(
                    state / "integration",
                    state / "integration",
                    VerificationPolicy(
                        tuple(policy_commands), policy_id="brain-org-pilot-v1:" + task["id"]
                    ),
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
    exp = next((t for t in db.list_tasks() if t["id"] == "EXP-001"), None)
    proposal = config(state)["contracts"]["schema-validation"]
    digest = hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()
    acks = [
        db.get_decision("contract-ack/" + digest + "/" + team)
        for team in ("Experience", "Platform")
    ]
    if (
        exp
        and exp["state"] == "integrated"
        and all(acks)
        and db.get_contract("schema-validation")["version"] == 1
    ):
        db.put_contract("schema-validation", 2, proposal)
        log_event(
            state, "contract_activated", name="schema-validation", version=2, agreement_hash=digest
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
        with Codex(native, codex=cfg.get("codex_executable", "codex")) as account:
            last_read, limits = 0.0, None
            while time.monotonic() < end and not (state / "STOP").exists():
                reconcile(state, db)
                reap_expired(state, db)
                tasks = db.list_tasks()
                # Each verifier is serial and may block dispatch briefly; active workers
                # maintain their own leases independently and continue during verification.
                submitted = next((t for t in tasks if t["state"] == "submitted"), None)
                if submitted:
                    verify_one(state, submitted)
                    continue
                if time.monotonic() - last_read > 60:
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
                    resource_reason = decision.get("reason", "unknown")
                    if decision["slots"] == 0:
                        reason = "quota-unavailable"
                    elif any(t["state"] == "pending" for t in tasks):
                        reason = "blocked-dependencies"
                    else:
                        reason = "catalogue-exhausted"
                    fingerprint_data = {
                        "tasks": sorted(
                            (t["id"], t["state"], t.get("candidate_sha")) for t in tasks
                        ),
                        "resource_reason": resource_reason,
                        "reason": reason,
                    }
                    fingerprint = hashlib.sha256(
                        json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    handoff_key = "sol-handoff/" + fingerprint
                    if db.get_decision(handoff_key) is None:
                        db.put_decision(
                            handoff_key,
                            {
                                "owner": "Sol",
                                "status": "requested",
                                "reason": reason,
                                "resource_reason": resource_reason,
                                "fingerprint": fingerprint,
                            },
                        )
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
