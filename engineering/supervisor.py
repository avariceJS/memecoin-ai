"""Durable bounded engineering supervisor; never imports Brain runtime code.

Supervisor and attempts are independent OS processes. SQLite is the authority;
Codex conversations are replaceable caches. Only this controller accepts and
integrates results. Agent-generated commands execute through Seatbelt.
"""

from __future__ import annotations

import dataclasses
import errno
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .confinement import SandboxRunner, python_runtime_roots
from .git_gate import Candidate, GitGate
from .metered_codex import metered_client
from .org_state import (
    CONTRACT_TRANSITION_BATCH_MAX_PROPOSALS,
    CORTEX_RESEARCH_PROTOCOL,
    StaleAttempt,
    StateError,
    Store,
    _json,
    _json_equal,
)
from .processes import process_identity, terminate_owned
from .review_packet import MAX_DIFF_BYTES, MAX_PACKET_BYTES, build_review_packet
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


class VerificationOutcomeUncertain(RuntimeError):
    """The independent review dispatch may have reached Codex without a result."""


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


def _bound_command_runner_identity(command_binding):
    """Translate one immutable command pin into SandboxRunner identity arguments."""
    if command_binding is None:
        return {}
    return {
        "expected_executable_sha256": command_binding.executable_sha256,
        "expected_executable_cdhash": command_binding.runtime_cdhash,
        "expected_executable_main_path": command_binding.runtime_path,
        "expected_script_sha256": command_binding.script_sha256,
        "expected_script_path": command_binding.script_path,
    }


def _bound_command_requires_strict_stdout_utf8(command_binding) -> bool:
    return command_binding is not None and command_binding.result["kind"] == "acceptance_json"


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


class IndependentReviewDecisionError(ValueError):
    """The independent reviewer returned an ambiguous or malformed decision."""


def _unique_review_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IndependentReviewDecisionError("review_decision_duplicate_key")
        result[key] = value
    return result


def _reject_review_constant(_value):
    raise IndependentReviewDecisionError("review_decision_nonstandard_number")


def _finite_review_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise IndependentReviewDecisionError("review_decision_nonstandard_number")
    return number


def parse_independent_review_decision(text):
    """Require one unambiguous decision object matching the local review contract."""
    if type(text) is not str:
        raise IndependentReviewDecisionError("review_decision_invalid_text")
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        opener = lines[0].strip() if lines else ""
        valid_opener = opener == "```" or (
            opener.startswith("```") and opener[3:].lower() == "json"
        )
        if len(lines) < 3 or not valid_opener or lines[-1].strip() != "```":
            raise IndependentReviewDecisionError("review_decision_invalid_fence")
        value = "\n".join(lines[1:-1])
    try:
        decision = json.loads(
            value,
            object_pairs_hook=_unique_review_object,
            parse_constant=_reject_review_constant,
            parse_float=_finite_review_float,
        )
    except IndependentReviewDecisionError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise IndependentReviewDecisionError("review_decision_invalid_json") from None
    if type(decision) is not dict or set(decision) != {
        "approved",
        "findings",
        "rationale",
    }:
        raise IndependentReviewDecisionError("review_decision_schema_invalid")
    if (
        type(decision["approved"]) is not bool
        or type(decision["findings"]) is not list
        or any(type(item) is not str for item in decision["findings"])
        or type(decision["rationale"]) is not str
    ):
        raise IndependentReviewDecisionError("review_decision_schema_invalid")
    return decision


class LeadDecisionError(ValueError):
    """The team lead returned an ambiguous or malformed decision."""


def _unique_lead_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LeadDecisionError("lead_result_duplicate_key")
        result[key] = value
    return result


def _reject_lead_constant(_value):
    raise LeadDecisionError("lead_result_nonstandard_number")


def _finite_lead_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise LeadDecisionError("lead_result_nonstandard_number")
    return number


def parse_lead_decision(text):
    """Require one exact, unambiguous lead response before ACK or worker admission."""
    if type(text) is not str:
        raise LeadDecisionError("lead_result_invalid_text")
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        opener = lines[0].strip() if lines else ""
        valid_opener = opener == "```" or (
            opener.startswith("```") and opener[3:].lower() == "json"
        )
        if len(lines) < 3 or not valid_opener or lines[-1].strip() != "```":
            raise LeadDecisionError("lead_result_invalid_fence")
        value = "\n".join(lines[1:-1])
    try:
        plan = json.loads(
            value,
            object_pairs_hook=_unique_lead_object,
            parse_constant=_reject_lead_constant,
            parse_float=_finite_lead_float,
        )
    except LeadDecisionError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise LeadDecisionError("lead_result_invalid_json") from None
    if type(plan) is not dict or set(plan) != {
        "decision",
        "worker_brief",
        "rationale",
        "contract_acks",
    }:
        raise LeadDecisionError("lead_result_shape")
    if (
        type(plan["decision"]) is not str
        or plan["decision"] not in ("approve", "blocked")
        or type(plan["worker_brief"]) is not str
        or type(plan["rationale"]) is not str
        or type(plan["contract_acks"]) is not list
    ):
        raise LeadDecisionError("lead_result_shape")
    return plan


def _configured_contract_transitions(cfg, db):
    """Persist exact configured proposals and retain their current publication state."""
    contracts = cfg.get("contracts", {})
    if type(contracts) is not dict:
        raise StateError("contract configuration invalid")
    if len(contracts) > CONTRACT_TRANSITION_BATCH_MAX_PROPOSALS:
        raise StateError("contract configuration exceeds transition limit")
    if any(type(name) is not str for name in contracts):
        raise StateError("contract configuration invalid")
    for name, proposal in contracts.items():
        cortex_wrapper_keys = {
            "version",
            "producer",
            "producer_task_id",
            "consumers",
            "proposal_body",
        }
        if (
            not name.strip()
            or type(proposal) is not dict
            or (
                name == CORTEX_RESEARCH_PROTOCOL
                and (
                    set(proposal) != cortex_wrapper_keys
                    or type(proposal.get("proposal_body")) is not dict
                    or type(proposal.get("proposal_body", {}).get("version")) is not int
                    or proposal["proposal_body"]["version"] != proposal.get("version")
                    or type(proposal.get("consumers")) is not list
                )
            )
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
    try:
        contracts = json.loads(_json(contracts))
    except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
        raise StateError("contract configuration invalid") from None
    pending = []
    for name, proposal in sorted(contracts.items()):
        if name == CORTEX_RESEARCH_PROTOCOL and (
            set(proposal) != cortex_wrapper_keys
            or type(proposal.get("proposal_body")) is not dict
            or type(proposal["proposal_body"].get("version")) is not int
            or proposal["proposal_body"]["version"] != proposal.get("version")
            or type(proposal.get("consumers")) is not list
        ):
            raise StateError("contract configuration invalid")
        contract_body = proposal["proposal_body"] if name == CORTEX_RESEARCH_PROTOCOL else proposal
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
        try:
            already_active = current["version"] == proposal["version"] and _json(
                current["body"]
            ) == _json(contract_body)
        except (TypeError, ValueError, OverflowError, RecursionError, RuntimeError):
            raise StateError("contract configuration invalid") from None
        if transition is None and already_active:
            if proposal["version"] == 1:
                continue  # Initial publication does not require transition evidence.
            if (
                name == "schema-validation"
                and proposal["version"] == 2
                and proposal["producer"] == "Experience"
                and consumers == ["Platform"]
            ):
                transition_key = db._contract_transition_key(name, proposal["version"])
                participant_teams = ["Experience", "Platform"]
                orphaned = [transition_key + "/activated"]
                orphaned.extend(
                    db._contract_ack_key(name, proposal["version"], team)
                    for team in participant_teams
                )
                if any(db.get_decision(key) is not None for key in orphaned):
                    raise StateError("legacy contract publication has orphan transition evidence")
                continue  # Exact supported legacy schema-validation v2 publication.
            raise StateError("contract transition publication evidence missing")
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
        pending.append(
            {
                "proposal": {
                    "name": name,
                    "from_version": proposal["version"] - 1,
                    "to_version": proposal["version"],
                    "body": contract_body,
                    "producer_task_id": producer_task_id,
                    "producer_team": proposal["producer"],
                    "consumer_teams": consumers,
                },
                "already_active": already_active,
            }
        )
    persisted = db.propose_contract_transitions([item["proposal"] for item in pending])
    return [
        {
            "name": transition["body"]["name"],
            "version": transition["body"]["to_version"],
            "body": transition["body"],
            "already_active": item["already_active"],
        }
        for item, transition in zip(pending, persisted, strict=True)
    ]


def _validate_lead_contract_acks(value, *, team, transitions):
    """Keep legacy response shape while refusing to treat task leads as reviewers."""
    if type(value) is not list or type(team) is not str:
        raise ValueError("lead_contract_ack_shape")
    if value:
        raise ValueError("lead_contract_ack_requires_independent_review")
    return []


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
    """Return strict UTF-8 Git output without text-mode newline normalization or trimming."""
    output = _git_output(repo, *args, raw=True)
    try:
        return output.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeError("git_path_not_utf8") from None


def git_raw_limited(repo, max_bytes, *args, timeout=30):
    """Capture strict UTF-8 Git output with bounded stdout, stderr and runtime."""
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")
    if (
        type(timeout) not in (int, float)
        or (type(timeout) is float and not math.isfinite(timeout))
        or timeout <= 0
        or timeout > 300
    ):
        raise ValueError("timeout must be in (0, 300]")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
    )
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repo),
        *args,
    ]
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    process = None
    output = bytearray()
    diagnostic = bytearray()
    failure = None
    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("git_output_timeout")
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    if len(output) + len(chunk) > max_bytes:
                        raise RuntimeError("git_output_exceeds_limit")
                    output.extend(chunk)
                else:
                    diagnostic.extend(chunk[: max(0, 1500 - len(diagnostic))])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("git_output_timeout")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise RuntimeError("git_output_timeout") from None
        if returncode:
            message = bytes(diagnostic[-1500:]).decode("utf-8", errors="replace")
            raise RuntimeError(message)
        try:
            return bytes(output).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RuntimeError("git_path_not_utf8") from None
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_error = None
        try:
            selector.close()
        except BaseException as exc:
            cleanup_error = exc
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    if process.poll() is None:
                        process.kill()
                    else:
                        raise
                process.wait(timeout=5)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        finally:
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except BaseException as exc:
                            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            if failure is not None:
                failure.add_note("Git capture cleanup also failed: " + type(cleanup_error).__name__)
            else:
                raise RuntimeError("git_process_cleanup_failed") from cleanup_error


def _verification_run_dir(state: Path, candidate_sha: str) -> Path:
    """Create an exclusive artifact directory for one verification invocation."""
    candidate_root = Path(state) / "verification" / candidate_sha
    candidate_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=candidate_root))


def config(state):
    deployment = validate_deployment(state)
    expected_module = (Path(deployment["controller"]) / "engineering" / "supervisor.py").resolve()
    if Path(__file__).resolve() != expected_module:
        raise RuntimeError("supervisor_module_outside_frozen_controller")
    return deployment["config"]


def store(state, *, controller_catalogue=None, require_company_control=False):
    return Store(
        state / "organization.sqlite",
        controller_catalogue=controller_catalogue,
        require_company_control=require_company_control,
    )


@contextmanager
def _company_operation_lock(state):
    path = Path(state) / ".company-dispatch.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _company_operation_guard(state, db, *, allowed_phase="running"):
    with _company_operation_lock(state):
        db.require_company_phase(allowed_phase)
        yield


@contextmanager
def _timed_exclusive_lock(path, deadline):
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    acquired = False
    try:
        os.fchmod(fd, 0o600)
        while not acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("company_hibernation_lock_timeout") from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        else:
            os.close(fd)


def _company_handoff_file_hashes(state):
    paths = []
    for directory in ("intents", "receipts"):
        root = state / directory
        if root.exists():
            paths.extend(sorted(item for item in root.iterdir() if item.is_file()))
    for root in sorted((state / "attempts").glob("[0-9]*")):
        for name in (
            "packet.json",
            "processes.json",
            "lead.json",
            "lead-raw.json",
            "worker.json",
            "worker-raw.json",
        ):
            path = root / name
            if path.exists():
                paths.append(path)
    if len(paths) > 5000:
        raise StateError("company_handoff_file_inventory_overflow")
    result = []
    total_bytes = 0
    for path in sorted(set(paths)):
        if path.is_symlink() or not path.is_file():
            raise StateError("company_handoff_file_identity_invalid")
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > 128 * 1024 * 1024:
            raise StateError("company_handoff_file_inventory_overflow")
        result.append(
            {
                "path": str(path.relative_to(state)),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return result


def _company_handoff_process_inventory(state, db):
    from .processes import process_identity as current_process_identity

    attempts = db.list_attempts()
    by_pid = {}
    for attempt in attempts:
        child = db.attempt_child_start_identity(attempt["id"], attempt["epoch"])
        identities = []
        if child is not None:
            identities.append(child)
        session = attempt.get("session")
        runner = session.get("runner") if isinstance(session, dict) else None
        if isinstance(runner, dict):
            for pid_key, birth_key in (("pid", "birth"), ("app_server_pid", "app_server_birth")):
                pid, birth = runner.get(pid_key), runner.get(birth_key)
                if pid is None and birth is None:
                    continue
                if type(pid) is not int or pid <= 1 or type(birth) is not str or not birth:
                    raise StateError("company_handoff_process_identity_unknown")
                identities.append({"pid": pid, "birth": birth})
        process_file = state / "attempts" / str(attempt["id"]) / "processes.json"
        if process_file.exists():
            if process_file.is_symlink() or not process_file.is_file():
                raise StateError("company_handoff_process_inventory_invalid")
            process_items = json.loads(process_file.read_text(encoding="utf-8"))
            if type(process_items) is not list:
                raise StateError("company_handoff_process_inventory_invalid")
            for item in process_items:
                if (
                    type(item) is not dict
                    or set(item) != {"pid", "birth"}
                    or type(item["pid"]) is not int
                    or item["pid"] <= 1
                    or type(item["birth"]) is not str
                    or not item["birth"]
                ):
                    raise StateError("company_handoff_process_identity_unknown")
                identities.append(item)
        for item in identities:
            prior = by_pid.get(item["pid"])
            if prior is not None and prior["birth"] != item["birth"]:
                raise StateError("company_handoff_process_identity_conflict")
            by_pid[item["pid"]] = dict(item)
    inventory = []
    for pid, owner in sorted(by_pid.items()):
        current = current_process_identity(pid)
        if current is None and platform.system() != "Darwin":
            raise StateError("company_handoff_process_identity_unqualified")
        inventory.append({**owner, "alive": current == owner["birth"]})
    return inventory


def _company_handoff_dispatches(state):
    from .usage_ledger import UsageLedger

    path = Path(state) / "usage-ledger.sqlite"
    if not path.exists():
        return {"available": False, "items": []}
    ledger = UsageLedger(path)
    try:
        rows = ledger.db.execute(
            "SELECT dispatch_id,identity,model,provider_turn_id,status,epoch "
            "FROM dispatches ORDER BY dispatch_id"
        ).fetchall()
        if len(rows) > 5000:
            raise StateError("company_handoff_dispatch_inventory_overflow")
        return {
            "available": True,
            "items": [dict(row) for row in rows],
        }
    finally:
        ledger.close()


def hibernate_company(state: Path, *, request_id: str | None = None, max_seconds=1800):
    """Fence, drain and checkpoint the local synthetic company without clearing Pilot STOP."""
    if type(max_seconds) is not int or not 1 <= max_seconds <= 3600:
        raise ValueError("company_hibernation_timeout_invalid")
    state = Path(state).resolve()
    cfg = config(state)
    db = store(state, controller_catalogue=cfg.get("catalogue"), require_company_control=True)
    deadline = time.monotonic() + max_seconds
    try:
        current = db.company_control_state(required=True)
        if current["phase"] == "hibernated":
            if request_id is not None and request_id != current["request_id"]:
                raise StateError("company_hibernation_request_conflict")
            return {
                "phase": "hibernated",
                "request_id": current["request_id"],
                "checkpoint_id": current["checkpoint_id"],
            }
        if current["phase"] == "draining":
            if request_id is not None and request_id != current["request_id"]:
                raise StateError("company_hibernation_request_conflict")
            request_id = current["request_id"]
        elif current["phase"] == "running":
            request_id = request_id or ("founder-" + uuid.uuid4().hex)
            current = db.request_company_hibernation(request_id)
        else:
            raise StateError("company_control_transition_invalid")

        handoff = state / "company-handoffs"
        handoff.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(handoff, 0o700)
        checkpoint_path = handoff / f"generation-{current['generation']}.json"
        with _timed_exclusive_lock(state / "supervisor.lock", deadline):
            with _timed_exclusive_lock(state / ".company-dispatch.lock", deadline):
                while True:
                    snapshot = db.company_hibernation_snapshot()
                    processes = _company_handoff_process_inventory(state, db)
                    running_tasks = [
                        task["id"] for task in snapshot["tasks"] if task["state"] == "running"
                    ]
                    live_processes = [item for item in processes if item["alive"]]
                    if not running_tasks and not live_processes:
                        break
                    if time.monotonic() >= deadline:
                        return {
                            "phase": "draining",
                            "request_id": request_id,
                            "reason": "active_work_or_processes_remain",
                            "running_tasks": running_tasks,
                            "live_processes": live_processes,
                        }
                    time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

                active_tip = git(state / "integration", "rev-parse", GitGate.ACTIVE_REF)
                main_tip = git(state / "integration", "rev-parse", "refs/heads/main")
                handoff_body = {
                    "schema": "company-hibernation-handoff-v1",
                    "request_id": request_id,
                    "generation": current["generation"],
                    "store": snapshot,
                    "processes": processes,
                    "dispatches": _company_handoff_dispatches(state),
                    "git": {
                        "active_ref": GitGate.ACTIVE_REF,
                        "active_tip": active_tip,
                        "main_tip": main_tip,
                        "integration_status": git(
                            state / "integration", "status", "--porcelain", "--untracked-files=all"
                        ),
                    },
                    "config_sha256": hashlib.sha256((state / "config.json").read_bytes()).hexdigest(),
                    "controller_manifest_sha256": hashlib.sha256(
                        (state / "controller-manifest.json").read_bytes()
                    ).hexdigest(),
                    "pilot_stop": {
                        "present": (state / "STOP").exists(),
                        "sha256": (
                            hashlib.sha256((state / "STOP").read_bytes()).hexdigest()
                            if (state / "STOP").is_file()
                            else None
                        ),
                    },
                    "files": _company_handoff_file_hashes(state),
                }
                canonical = _json(handoff_body).encode("utf-8")
                if len(canonical) > 8 * 1024 * 1024:
                    raise StateError("company_handoff_checkpoint_too_large")
                checkpoint_id = hashlib.sha256(canonical).hexdigest()
                envelope = {"checkpoint_id": checkpoint_id, "handoff": handoff_body}
                atomic(checkpoint_path, envelope)
                final = db.complete_company_hibernation(
                    request_id=request_id,
                    generation=current["generation"],
                    checkpoint_id=checkpoint_id,
                    expected_revision=snapshot["revision"],
                    expected_event_seq=snapshot["event_seq"],
                )
                return {
                    "phase": final["phase"],
                    "request_id": request_id,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_path": str(checkpoint_path),
                    "task_count": len(snapshot["tasks"]),
                    "attempt_count": len(snapshot["attempts"]),
                    "unknown_dispatch_count": sum(
                        item["status"] in {"unknown", "timeout", "failed"}
                        for item in handoff_body["dispatches"]["items"]
                    ),
                    "pilot_stop_preserved": handoff_body["pilot_stop"]["present"],
                }
    finally:
        db.close()


def continue_company(state: Path, *, request_id: str | None = None):
    """Explicitly validate one hibernation handoff, locally reconcile, then reopen Luna claims."""
    state = Path(state).resolve()
    cfg = config(state)
    db = store(state, controller_catalogue=cfg.get("catalogue"), require_company_control=True)
    try:
        control = db.company_control_state(required=True)
        if control["phase"] == "running":
            return {"phase": "running", "status": "already_running"}
        if control["phase"] == "wake_pending":
            if request_id is not None and request_id != control["request_id"]:
                raise StateError("company_resume_request_conflict")
            request_id = control["request_id"]
        elif control["phase"] != "hibernated":
            raise StateError("company_not_hibernated")
        if (state / "STOP").exists():
            return {"phase": control["phase"], "status": "blocked_by_pilot_STOP"}
        checkpoint_id = control["checkpoint_id"]
        handoff_generation = control["generation"] - (1 if control["phase"] == "wake_pending" else 0)
        checkpoint_path = state / "company-handoffs" / f"generation-{handoff_generation}.json"
        envelope = read(checkpoint_path)
        handoff = envelope.get("handoff") if isinstance(envelope, dict) else None
        if (
            type(handoff) is not dict
            or envelope.get("checkpoint_id") != checkpoint_id
            or hashlib.sha256(_json(handoff).encode("utf-8")).hexdigest() != checkpoint_id
            or handoff.get("schema") != "company-hibernation-handoff-v1"
            or handoff.get("generation") != handoff_generation
            or handoff.get("request_id")
            != handoff.get("store", {}).get("control", {}).get("request_id")
            or control.get("checkpoint_id") != checkpoint_id
        ):
            raise StateError("company_hibernation_checkpoint_invalid")
        if (
            hashlib.sha256((state / "config.json").read_bytes()).hexdigest()
            != handoff["config_sha256"]
            or hashlib.sha256((state / "controller-manifest.json").read_bytes()).hexdigest()
            != handoff["controller_manifest_sha256"]
        ):
            raise StateError("company_hibernation_configuration_changed")
        if git(state / "integration", "rev-parse", "refs/heads/main") != handoff["git"]["main_tip"]:
            raise StateError("company_hibernation_main_changed")
        if control["phase"] == "hibernated" and (
            git(state / "integration", "rev-parse", GitGate.ACTIVE_REF)
            != handoff["git"]["active_tip"]
            or git(state / "integration", "status", "--porcelain", "--untracked-files=all")
            != handoff["git"]["integration_status"]
            or db.db.execute("SELECT value FROM quiescence_revision WHERE id=1").fetchone()[0]
            != handoff["store"]["revision"]
            or _json(db.list_tasks()) != _json(handoff["store"]["tasks"])
            or _json(db.list_attempts()) != _json(handoff["store"]["attempts"])
            or _json(_company_handoff_file_hashes(state)) != _json(handoff["files"])
            or _json(_company_handoff_dispatches(state)) != _json(handoff["dispatches"])
        ):
            raise StateError("company_hibernation_store_or_custody_changed")
        processes = _company_handoff_process_inventory(state, db)
        if any(item["alive"] for item in processes) or any(
            item.get("alive") for item in handoff.get("processes", [])
        ):
            raise StateError("company_hibernation_process_still_live")
        if control["phase"] == "hibernated":
            transition_events = db.events(handoff["store"]["event_seq"])
            if (
                len(transition_events) != 1
                or transition_events[0]["kind"] != "company_control_transition"
                or transition_events[0]["entity"] != "company/control"
                or transition_events[0]["body"].get("phase") != "hibernated"
                or transition_events[0]["body"].get("checkpoint_id") != checkpoint_id
            ):
                raise StateError("company_hibernation_history_changed")
            request_id = request_id or ("founder-wake-" + uuid.uuid4().hex)
            control = db.request_company_resume(request_id)

        # Reconciliation is local and receipt-bound. Wake-pending permits only this repair
        # path; claims, planning and provider sends remain fenced until the final Store CAS.
        with _timed_exclusive_lock(state / "supervisor.lock", time.monotonic() + 30):
            if (state / "STOP").exists():
                return {"phase": "wake_pending", "status": "blocked_by_pilot_STOP"}
            reconcile(state, db, allow_wake_pending=True)
            if (state / "STOP").exists():
                return {"phase": "wake_pending", "status": "blocked_by_pilot_STOP"}
            if (
                hashlib.sha256((state / "config.json").read_bytes()).hexdigest()
                != handoff["config_sha256"]
                or hashlib.sha256((state / "controller-manifest.json").read_bytes()).hexdigest()
                != handoff["controller_manifest_sha256"]
            ):
                raise StateError("company_hibernation_configuration_changed")
            revision = db.db.execute(
                "SELECT value FROM quiescence_revision WHERE id=1"
            ).fetchone()[0]
            event_seq = db.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            resumed = db.complete_company_resume(
                request_id=request_id,
                generation=control["generation"],
                checkpoint_id=checkpoint_id,
                expected_revision=revision,
                expected_event_seq=event_seq,
            )
        return {
            "phase": resumed["phase"],
            "request_id": request_id,
            "checkpoint_id": checkpoint_id,
            "status": "explicit_resume_ready",
            "sol_oversight": "restored_by_the_supervisor_sol_lead_gate",
            "pilot_stop_preserved": (state / "STOP").exists(),
        }
    finally:
        db.close()


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

    def terminate_and_confirm(pid, birth):
        if pid is None and birth is None:
            return True
        if type(pid) is not int or pid <= 1 or type(birth) is not str or not birth:
            raise StateError("expired_process_identity_invalid")
        if platform.system() != "Darwin":
            raise StateError("expired_process_identity_unverifiable")
        signalled = terminate_owned(pid, birth)
        deadline = time.monotonic() + 5.0
        while True:
            current = process_identity(pid)
            if current is not None and current != birth:
                return True
            if current is None:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                except PermissionError:
                    raise StateError("expired_process_identity_unverifiable") from None
                except OSError as exc:
                    if exc.errno == errno.ESRCH:
                        return True
                    raise StateError("expired_process_identity_unverifiable") from None
                raise StateError("expired_process_identity_unverifiable")
            if not signalled:
                raise StateError("expired_process_still_owned")
            if time.monotonic() >= deadline:
                raise StateError("expired_process_termination_unconfirmed")
            time.sleep(0.05)

    for attempt in db.list_attempts():
        if attempt["state"] != "expired":
            continue
        marker = state / "attempts" / str(attempt["id"]) / "reaped.json"
        confirmation = db.attempt_process_reap_confirmation(attempt["id"], attempt["epoch"])
        if confirmation is not None:
            if not marker.exists():
                atomic(
                    marker,
                    {
                        "at": time.time(),
                        "attempt_id": attempt["id"],
                        "disposition": confirmation["disposition"],
                        "process_identities": confirmation["process_identities"],
                        "killed": {
                            str(item["pid"]): True for item in confirmation["process_identities"]
                        },
                    },
                )
            continue
        session = attempt.get("session") or {}
        child_identity = db.attempt_child_start_identity(attempt["id"], attempt["epoch"])
        runner = session.get("runner")
        identities = {}
        if child_identity is not None:
            identities["attempt"] = child_identity
        if runner is not None:
            if (
                type(runner) is not dict
                or type(runner.get("pid")) is not int
                or runner["pid"] <= 1
                or type(runner.get("birth")) is not str
                or not runner["birth"]
            ):
                raise StateError("expired_session_process_identity_invalid")
            session_child = {"pid": runner["pid"], "birth": runner["birth"]}
            if child_identity is not None and child_identity != session_child:
                raise StateError("expired_session_child_owner_mismatch")
            identities["attempt"] = session_child
            app_pid, app_birth = runner.get("app_server_pid"), runner.get("app_server_birth")
            if app_pid is not None or app_birth is not None:
                if (
                    type(app_pid) is not int
                    or app_pid <= 1
                    or type(app_birth) is not str
                    or not app_birth
                ):
                    raise StateError("expired_session_process_identity_invalid")
                identities["app_server"] = {"pid": app_pid, "birth": app_birth}
        process_identities = {}
        for identity in identities.values():
            process_identities[identity["pid"]] = identity
        process_file = marker.parent / "processes.json"
        if process_file.exists():
            for item in read(process_file):
                identity = db._validate_child_identity(
                    {"pid": item.get("pid"), "birth": item.get("birth")}
                )
                previous = process_identities.get(identity["pid"])
                if previous is not None and previous != identity:
                    raise StateError("expired_process_identity_conflict")
                process_identities[identity["pid"]] = identity
        process_identities = [process_identities[pid] for pid in sorted(process_identities)]
        killed = {
            str(identity["pid"]): terminate_and_confirm(identity["pid"], identity["birth"])
            for identity in process_identities
        }
        disposition = "processes_reaped" if process_identities else "no_work_before_custody"
        db.record_attempt_process_reaped(
            attempt["id"],
            attempt["epoch"],
            process_identities,
            disposition=disposition,
        )
        atomic(
            marker,
            {
                "at": time.time(),
                "attempt_id": attempt["id"],
                "disposition": disposition,
                "process_identities": process_identities,
                "killed": killed,
            },
        )
        log_event(state, "expired_processes_reconciled", attempt_id=attempt["id"], killed=killed)
    return expired


def run_attempt(state: Path, attempt_id: int):
    cfg = config(state)
    db = store(state, controller_catalogue=cfg.get("catalogue"), require_company_control=True)
    attempt = next(a for a in db.list_attempts() if a["id"] == attempt_id)
    task = db.get_task(attempt["task_id"])
    epoch = attempt["epoch"]
    child_identity = {"pid": os.getpid(), "birth": process_identity(os.getpid())}
    if child_identity["birth"] is None:
        log_event(
            state,
            "attempt_child_start_unidentified",
            attempt_id=attempt_id,
            task=task["id"],
        )
        db.close()
        return
    try:
        child_started = db.record_attempt_child_start(attempt_id, epoch, child_identity)
    except (StaleAttempt, StateError) as exc:
        log_event(
            state,
            "attempt_child_start_rejected",
            attempt_id=attempt_id,
            task=task["id"],
            error_type=type(exc).__name__,
        )
        db.close()
        return
    if not child_started:
        log_event(
            state,
            "attempt_child_start_duplicate",
            attempt_id=attempt_id,
            task=task["id"],
        )
        db.close()
        return
    try:
        db.require_company_running()
        company_running = True
    except StateError:
        company_running = False
    if (state / "STOP").exists() or not company_running:
        try:
            db.stop_started_child_before_work(attempt_id, epoch, child_identity)
        except (StaleAttempt, StateError) as exc:
            log_event(
                state,
                "attempt_stop_before_work_deferred",
                attempt_id=attempt_id,
                task=task["id"],
                error_type=type(exc).__name__,
            )
        db.close()
        return
    # A queued OS child may start after its lease was already replaced.
    db.renew(attempt_id, epoch, LEASE)
    try:
        db.require_controller_parse_recovery_clear()
    except StateError as exc:
        reason = (
            "controller_circuit_open:structured_parse"
            if str(exc) == "controller_parse_recovery_open"
            else "controller_recovery_history_invalid"
        )
        db.fail(attempt_id, epoch, reason, retryable=False)
        db.close()
        return
    try:
        from .planning import require_controller_assignment

        require_controller_assignment(cfg, db, task)
    except StateError as exc:
        reason = str(exc)
        log_event(
            state,
            "attempt_blocked",
            task=task["id"],
            attempt_id=attempt_id,
            epoch=epoch,
            reason=reason,
        )
        db.fail(attempt_id, epoch, "controller_assignment_unavailable:" + reason, retryable=False)
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
    processes = [child_identity]
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
        if not db.record_attempt_work_start(attempt_id, epoch, child_identity, stage="lead"):
            raise StateError("attempt_lead_dispatch_already_started")
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
                    "contract and worker acceptance. Review each item in proposed_contracts, "
                    "but do not acknowledge interface transitions here; a separately persisted "
                    "independent contract-review lane owns those approvals. Return an empty "
                    "contract_acks list. Return JSON "
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
                plan = parse_lead_decision(out["text"])
                plan["contract_acks"] = _validate_lead_contract_acks(
                    plan["contract_acks"], team=task["team"], transitions=team_transitions
                )
            except Exception as exc:
                lead.record_outcome(
                    f"attempt:{attempt_id}:lead", "failed", {"parser_error": type(exc).__name__}
                )
                controller_parse_fault("lead", exc)
            atomic(run / "lead.json", {**out, "plan": plan, "thread": thread})
            db.record_lead_result(
                attempt_id,
                epoch,
                thread=thread,
                model=SOL,
                base=base,
                plan=plan,
            )
            if plan.get("decision") != "approve":
                raise RuntimeError("lead_blocked")
        if not db.record_attempt_work_start(attempt_id, epoch, child_identity, stage="worker"):
            raise StateError("attempt_worker_dispatch_already_started")
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
                retryable=(
                    not db.attempt_work_started(attempt_id, epoch)
                    and "lead_blocked" not in str(exc)
                    and "controller_fault:" not in str(exc)
                ),
                outcome_uncertain=db.attempt_work_started(attempt_id, epoch),
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
    cfg = config(state)
    db = store(state, controller_catalogue=cfg.get("catalogue"), require_company_control=True)
    task = db.get_task(task["id"])
    try:
        db.require_company_running()
    except StateError:
        db.close()
        return False
    data = db.get_candidate(task["id"])
    evidence = data["evidence"]
    python = cfg["python"]
    try:
        from .planning import require_controller_assignment

        require_controller_assignment(cfg, db, task)
        admission = db.get_decision("admission/" + task["id"])
        nomination = None
        if admission is not None:
            admission_body = admission.get("body")
            if type(admission_body) is not dict:
                raise ValueError("invalid_task_admission_record")
            nomination = admission_body.get("department_nomination")
            if nomination is not None and (
                type(nomination) is not dict
                or nomination.get("card_id") != task["id"]
                or nomination.get("team") != task.get("team")
            ):
                raise ValueError("invalid_task_department_nomination")
        task = {**task, "department_nomination": nomination}
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
    review_dispatch_started = False
    review_raw_durable = False
    try:
        candidate = gate.submit(
            Path(evidence["checkout"]), task["candidate_sha"], evidence["base"], task["scope"]
        )
    except Exception as exc:
        db.verify(task["id"], task["candidate_sha"], False, {"error": str(exc)[:2000]})
        db.close()
        return True
    log_event(state, "verification_started", task=task["id"], sha=candidate.sha)
    verification_run = _verification_run_dir(state, candidate.sha)

    def verification(checkout, sha, fixed_policy):
        nonlocal review_dispatch_started, review_raw_durable
        scratch = verification_run / "scratch"
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
            command_binding = (
                verifier.command_bindings[verifier_command_index] if verifier is not None else None
            )
            result = box.run(
                argv,
                cwd=checkout,
                timeout=90,
                max_output_bytes=200000,
                env_overrides=dict(fixed_policy.env),
                strict_stdout_utf8=_bound_command_requires_strict_stdout_utf8(command_binding),
                **_bound_command_runner_identity(command_binding),
            )
            results.append(dataclasses.asdict(result))
            atomic(verification_run / "commands.json", results)
            if result.returncode or result.timed_out or result.output_truncated:
                raise RuntimeError("verification_command_failed:" + json.dumps(results[-1])[-2500:])
            if verifier is None and command_index == 0:
                validate_verifier_result(
                    result.stdout,
                    {
                        "acceptance_id": task["id"],
                        "result_schema": "org-pilot-acceptance-v1",
                        "case_field": "case",
                        "passed_field": "passed",
                    },
                )
            elif (
                command_binding is not None and command_binding.result["kind"] == "acceptance_json"
            ):
                validate_verifier_result(
                    result.stdout,
                    {
                        "acceptance_id": command_binding.result["acceptance_id"],
                        "result_schema": command_binding.result["result_schema"],
                        "case_field": command_binding.result["case_field"],
                        "passed_field": command_binding.result["passed_field"],
                    },
                )
        diff = git_raw_limited(
            state / "integration",
            MAX_DIFF_BYTES,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            candidate.integration_tip,
            sha,
        )
        merged_path_output = git_raw_limited(
            state / "integration",
            MAX_PACKET_BYTES,
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
        atomic(verification_run / "review-packet.json", review_packet)
        native = verification_run / "native"
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
            try:
                review_dispatch_started = True
                thread = reviewer.start(
                    SOL,
                    RULES + "\nYou are the independent verification reviewer. "
                    "You did not author this patch. No editing.",
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
            except Exception as exc:
                raise VerificationOutcomeUncertain(type(exc).__name__) from exc
            atomic(verification_run / "review-raw.json", review)
            review_raw_durable = True
            try:
                decision = parse_independent_review_decision(review["text"])
            except Exception as exc:
                reviewer.record_outcome(
                    f"verify:{task['id']}:{sha}", "failed", {"parser_error": type(exc).__name__}
                )
                raise
            atomic(
                verification_run / "review.json",
                {**review, "decision": decision, "thread": thread, "model": SOL},
            )
            if decision.get("approved") is not True:
                raise RuntimeError("independent_review_rejected:" + json.dumps(decision))
        return {
            "status": "passed",
            "commands_sha256": hashlib.sha256(
                (verification_run / "commands.json").read_bytes()
            ).hexdigest(),
            "review_sha256": hashlib.sha256(
                (verification_run / "review.json").read_bytes()
            ).hexdigest(),
            "review_packet_sha256": review_packet["packet_sha256"],
            "review_thread": thread,
            "verification_run": verification_run.relative_to(state / "verification").as_posix(),
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
            if review_dispatch_started and not review_raw_durable:
                db.hold_uncertain_verification(
                    task["id"], task["candidate_sha"], {"error": str(exc)[-3000:]}
                )
            else:
                db.verify(task["id"], task["candidate_sha"], False, {"error": str(exc)[-3000:]})
    finally:
        db.close()
    return True


def reconcile(state, db, *, allow_wake_pending=False):
    try:
        if allow_wake_pending:
            db.require_company_phase("wake_pending")
        else:
            db.require_company_running()
    except StateError:
        return False
    for path in (state / "intents").glob("*.json"):
        intent = read(path)
        task = db.get_task(intent["task"])
        if task["candidate_sha"] != intent["submitted_sha"]:
            log_event(state, "reconciliation_blocked", task=intent["task"], reason="stale_intent")
            continue
        plan = None
        if task["state"] in ("submitted", "verified", "accepted"):
            try:
                from .planning import require_controller_assignment

                require_controller_assignment(config(state), db, task)
            except ValueError as exc:
                log_event(
                    state,
                    "reconciliation_blocked",
                    task=task["id"],
                    reason=str(exc),
                )
                continue
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
            if (
                intent["task"] != task["id"]
                or intent["submitted_sha"] != task["candidate_sha"]
                or type(intent.get("attempt_id")) is not int
                or type(intent.get("epoch")) is not int
                or intent["attempt_id"] != candidate["attempt_id"]
                or intent["epoch"] != candidate["epoch"]
            ):
                log_event(
                    state,
                    "reconciliation_blocked",
                    task=task["id"],
                    reason="intent_attempt_binding_mismatch",
                )
                continue
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
            gate = GitGate(
                state / "integration",
                state / "integration",
                plan.policy,
                state / "receipts",
                operation_guard=lambda: _company_operation_guard(
                    state,
                    db,
                    allowed_phase="wake_pending" if allow_wake_pending else "running",
                ),
            )
            gate_candidate = Candidate(
                intent["submitted_sha"],
                candidate["evidence"].get("base", intent["expected_tip"]),
                intent["expected_tip"],
                intent["candidate_sha"],
                Path(),
                (),
            )
            gate.validate_candidate(gate_candidate)
            # Every recovery phase consumes the same immutable verifier receipt. In
            # particular, an already-moved active ref is not proof that verification
            # happened: the process may have stopped after CAS, or the intent may be
            # damaged. Keep receipt and policy binding ahead of all Store transitions.
            gate.load_receipt(intent["receipt"], intent["candidate_sha"])
            if task["state"] in ("verified", "accepted"):
                verification = db.get_verification(task["id"], intent["submitted_sha"])
                if (
                    verification is None
                    or not verification["passed"]
                    or not _json_equal(verification["details"], intent)
                ):
                    log_event(
                        state,
                        "reconciliation_blocked",
                        task=task["id"],
                        reason="verification_intent_binding_mismatch",
                    )
                    continue
        if task["state"] == "submitted":
            db.verify(task["id"], task["candidate_sha"], True, intent)
            task = db.get_task(task["id"])
        if task["state"] == "verified":
            db.accept(task["id"], intent["submitted_sha"])
            task = db.get_task(task["id"])
        if task["state"] == "accepted":
            tip = git(state / "integration", "rev-parse", "refs/heads/research/active")
            # Reuse GitGate's receipt, ancestry, and compare-and-swap checks on both
            # the first publish and recovery after a publish-before-Store crash.
            published = gate.publish(gate_candidate, intent["receipt"], tip)
            db.integrate(task["id"], intent["submitted_sha"], published, intent)
    return activate_contract(state, db)


def _contract_review_source(state, db, cfg, transition):
    """Resolve an integrated producer's exact candidate, verifier policy and receipt."""
    proposal = transition["body"]
    producer_id = proposal["producer_task_id"]
    task = db.get_task(producer_id)
    integration = db.get_integration(producer_id)
    if task["state"] != "integrated" or integration is None:
        raise StateError("contract producer is not durably integrated")
    if integration["candidate_sha"] != task["candidate_sha"]:
        raise StateError("contract producer integration candidate changed")
    verification = db.get_verification(producer_id, integration["candidate_sha"])
    candidate = db.get_candidate(producer_id, integration["candidate_sha"])
    if (
        verification is None
        or not verification["passed"]
        or candidate is None
        or type(candidate["evidence"]) is not dict
        or verification["attempt_id"] != candidate["attempt_id"]
        or verification["epoch"] != candidate["epoch"]
        or _json(verification["details"]) != _json(integration["details"])
    ):
        raise StateError("contract producer verification binding is invalid")
    intent = integration["details"]
    required_intent = {
        "task",
        "submitted_sha",
        "attempt_id",
        "epoch",
        "candidate_sha",
        "expected_tip",
        "receipt",
    }
    if (
        type(intent) is not dict
        or set(intent) != required_intent
        or intent["task"] != producer_id
        or intent["submitted_sha"] != integration["candidate_sha"]
        or intent["candidate_sha"] != integration["integration_sha"]
        or intent["attempt_id"] != candidate["attempt_id"]
        or type(intent["attempt_id"]) is not int
        or intent["epoch"] != candidate["epoch"]
        or type(intent["epoch"]) is not int
        or type(intent["candidate_sha"]) is not str
        or not GitGate.SHA_RE.fullmatch(intent["candidate_sha"])
        or type(intent["expected_tip"]) is not str
        or not GitGate.SHA_RE.fullmatch(intent["expected_tip"])
        or type(intent["receipt"]) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", intent["receipt"])
    ):
        raise StateError("contract producer integration intent is invalid")
    checkout_value = candidate["evidence"].get("checkout")
    if type(checkout_value) is not str or not Path(checkout_value).is_absolute():
        raise StateError("contract producer checkout identity is invalid")
    checkout = Path(checkout_value)
    if (
        len(checkout.parents) < 3
        or checkout.resolve()
        != (state.resolve() / "attempts" / str(candidate["attempt_id"]) / "checkout").resolve()
    ):
        raise StateError("contract producer checkout is outside its attempt state")
    plan_state = checkout.parents[2]
    plan = prepare_verification_plan(plan_state, db, task, cfg)
    matches, reason = attempt_plan_matches(plan, candidate["evidence"])
    if not matches or not plan.execution_qualified:
        raise StateError("contract producer verifier plan is unavailable: " + str(reason))
    candidate_base = candidate["evidence"].get("base")
    if type(candidate_base) is not str or not GitGate.SHA_RE.fullmatch(candidate_base):
        raise StateError("contract producer base identity is invalid")
    gate = GitGate(
        state / "integration",
        state / "integration",
        plan.policy,
        state / "receipts",
    )
    gate_candidate = Candidate(
        intent["submitted_sha"],
        candidate_base,
        intent["expected_tip"],
        intent["candidate_sha"],
        Path(),
        (),
    )
    gate.validate_candidate(gate_candidate)
    active_tip = git(state / "integration", "rev-parse", GitGate.ACTIVE_REF)
    git(state / "integration", "merge-base", "--is-ancestor", intent["candidate_sha"], active_tip)
    gate.load_receipt(intent["receipt"], intent["candidate_sha"])
    if git(state / "integration", "rev-parse", GitGate.ACTIVE_REF) != active_tip:
        raise StateError("contract producer active ref moved during evidence validation")
    return {
        "source_commit": intent["candidate_sha"],
        "receipt": intent["receipt"],
        "policy_sha256": plan.policy.digest,
        "plan_sha256": plan.plan_sha256,
        "active_tip": active_tip,
    }


def _contract_review_evidence_revision(db, proposal, source):
    identity = {key: value for key, value in source.items() if key != "active_tip"}
    return hashlib.sha256(
        _json(
            {
                "proposal_digest": db._contract_review_digest(proposal),
                **identity,
            }
        ).encode("utf-8")
    ).hexdigest()


def _contract_review_dispatch_guard(state, db, cfg, request):
    """Revalidate STOP and source evidence at the provider invocation boundary."""
    if (state / "STOP").exists():
        raise StateError("contract review dispatch blocked by STOP")
    db.require_company_running()
    body = request["body"]
    transitions = _configured_contract_transitions(cfg, db)
    transition = next(
        (
            item
            for item in transitions
            if item["name"] == body["name"]
            and item["version"] == body["version"]
            and not item["already_active"]
        ),
        None,
    )
    if transition is None:
        raise StateError("contract review transition is no longer current")
    participants = [transition["body"]["producer_team"], *transition["body"]["consumer_teams"]]
    if body["team"] not in participants:
        raise StateError("contract review participant is no longer current")
    source = _contract_review_source(state, db, cfg, transition)
    expected_revision = _contract_review_evidence_revision(db, transition["body"], source)
    evidence_ref = "git-receipt:" + source["receipt"]
    if (
        body["evidence_revision"] != expected_revision
        or body["contract_hash"] != transition["body"]["contract_hash"]
        or body["packet"]["source_commit"] != source["source_commit"]
        or body["packet"]["evidence_refs"] != [evidence_ref]
    ):
        raise StateError("contract review source changed before dispatch")


def _prepare_contract_review_requests(state, db, cfg, transitions):
    """Create idempotent review requests only from a current integrated producer receipt."""
    if (state / "STOP").exists():
        return {}
    prepared = {}
    for transition in transitions:
        if transition["already_active"]:
            continue
        name, version = transition["name"], transition["version"]
        key = (name, version)
        try:
            source = _contract_review_source(state, db, cfg, transition)
            proposal = transition["body"]
            evidence_revision = _contract_review_evidence_revision(db, proposal, source)
            evidence = {
                "ref": "git-receipt:" + source["receipt"],
                "sha256": source["receipt"],
            }
            analysis = (
                f"Controller evidence binds producer task {proposal['producer_task_id']} to "
                f"integrated commit {source['source_commit']} and verifier receipt "
                f"{source['receipt']} under policy {source['policy_sha256']}. This is source "
                "evidence only; participant compatibility and affected-consumer readiness have "
                "not yet been independently established."
            )
            requests = []
            for team in [proposal["producer_team"], *proposal["consumer_teams"]]:
                try:
                    request = db.prepare_contract_review(
                        name,
                        version,
                        team,
                        evidence_revision,
                        {
                            "source_commit": source["source_commit"],
                            "evidence_refs": [evidence],
                            "compatibility_analysis": analysis,
                            "migration_plan": None,
                        },
                    )
                    requests.append(request["body"]["request_id"])
                    continue
                except StateError as exc:
                    if str(exc) != "contract participant already acknowledged":
                        raise
                ack = db.get_decision(db._contract_ack_key(name, version, team))
                if ack is None:
                    raise StateError("acknowledged contract review request is missing")
                worklist = db.contract_review_worklist(limit=100)
                if worklist.get("overflow"):
                    raise StateError("contract review worklist exceeds bound")
                matches = []
                for item in worklist["items"]:
                    if item["team"] != team:
                        continue
                    existing = db.get_contract_review_request(item["request_id"])
                    body = existing["body"]
                    packet = body["packet"]
                    if (
                        body["name"] == name
                        and body["version"] == version
                        and body["evidence_revision"] == evidence_revision
                        and body["contract_hash"] == proposal["contract_hash"]
                        and packet["source_commit"] == source["source_commit"]
                        and packet["evidence_refs"] == [evidence["ref"]]
                        and ack["body"].get("review_request_id") == body["request_id"]
                        and ack["body"].get("review_packet_hash") == body["packet_hash"]
                    ):
                        matches.append(body["request_id"])
                if len(matches) != 1:
                    raise StateError("acknowledged contract review evidence is ambiguous")
                requests.append(matches[0])
            prepared[key] = tuple(requests)
        except (OSError, RuntimeError, StateError, TypeError, ValueError) as exc:
            log_event(
                state,
                "contract_review_preparation_blocked",
                name=name,
                version=version,
                producer_task=transition["body"]["producer_task_id"],
                error=str(exc)[:256],
                dispatch_authority=False,
                provider_dispatch=False,
            )
    return prepared


def activate_contract(state, db):
    """Prepare evidence-bound reviews, then replay and activate current transitions."""
    if (state / "STOP").exists():
        return ()
    cfg = config(state)
    transitions = _configured_contract_transitions(cfg, db)
    prepared = _prepare_contract_review_requests(state, db, cfg, transitions)
    allowed_request_ids = set()
    for transition in transitions:
        key = (transition["name"], transition["version"])
        if transition["already_active"]:
            # Revalidate published acknowledgement provenance on every pass even though
            # no review dispatch is eligible for an already active contract.
            db.activate_contract_transition(transition["name"], transition["version"])
            continue
        request_ids = prepared.get(key)
        if request_ids is None:
            continue
        allowed_request_ids.update(request_ids)
        try:
            replay = db.replay_contract_review_responses_for_transition(
                transition["name"],
                transition["version"],
                transition["body"]["contract_hash"],
            )
        except StateError as exc:
            log_event(
                state,
                "contract_review_replay_failed",
                name=transition["name"],
                version=transition["version"],
                contract_hash=transition["body"]["contract_hash"],
                error=str(exc)[:256],
                dispatch_authority=False,
                provider_dispatch=False,
            )
        else:
            if replay["overflow"] or replay["failures"] or replay["replayed"]:
                log_event(
                    state,
                    "contract_review_replay_reconciled",
                    name=transition["name"],
                    version=transition["version"],
                    contract_hash=transition["body"]["contract_hash"],
                    response_recorded_count=replay["response_recorded_count"],
                    replayed=replay["replayed"],
                    failures=replay["failures"],
                    overflow=replay["overflow"],
                    dispatch_authority=replay["dispatch_authority"],
                    provider_dispatch=replay["provider_dispatch"],
                )
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
    return tuple(sorted(allowed_request_ids))


def _spawn_backoff_deadline(readiness):
    pending = [item for item in readiness if item["state"] == "pending"]
    if not pending:
        return None
    deadlines = []
    for item in pending:
        for blocker in item["blockers"]:
            if blocker.get("kind") != "attempt_spawn_backoff":
                continue
            retry_at = blocker.get("retry_at")
            if (
                isinstance(retry_at, bool)
                or not isinstance(retry_at, (int, float))
                or not math.isfinite(retry_at)
            ):
                raise StateError("attempt_spawn_backoff_readiness_invalid")
            deadlines.append(retry_at)
    return min(deadlines) if deadlines else None


def _spawn_backoff_plan_token(readiness, revision):
    backoff_tasks = tuple(
        sorted(
            (item["task_id"], blocker["retry_at"])
            for item in readiness
            if item["state"] == "pending"
            for blocker in item["blockers"]
            if blocker.get("kind") == "attempt_spawn_backoff"
        )
    )
    return revision, backoff_tasks


def supervise(
    state: Path,
    max_seconds=1800,
    *,
    contract_review_transport=None,
    department_plan_transport=None,
):
    from .resources import choose_capacity

    state = state.resolve()
    cfg = config(state)
    db = store(state, controller_catalogue=cfg.get("catalogue"), require_company_control=True)
    with (state / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log_event(state, "supervisor_started", birth=process_identity(os.getpid()))
        end = time.monotonic() + max_seconds
        blocked_verification = set()
        backoff_plan_token = None
        while time.monotonic() < end and not (state / "STOP").exists():
            try:
                db.require_company_running()
            except StateError:
                break
            allowed_contract_reviews = reconcile(state, db)
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
            active = [t for t in db.list_tasks() if t["state"] == "running"]
            from .planning import require_controller_assignment

            ready = []
            assignment_blocked = {}
            active_teams = {a["team"] for a in active}
            for task in db.ready_tasks():
                if task["team"] in active_teams:
                    continue
                try:
                    require_controller_assignment(cfg, db, task)
                except StateError as exc:
                    assignment_blocked[task["id"]] = str(exc)
                    continue
                ready.append(task)
            decision = choose_capacity(
                measured_usage(state, db),
                {
                    "available_memory_bytes": available_memory(),
                    "cpu_count": os.cpu_count(),
                    "active_team_ids": sorted({t["team"] for t in active}),
                    "active_attempt_count": len(active),
                },
                cfg["resources"],
                ready_tasks=ready,
                active_scopes=[s for t in active for s in t["scope"]],
            )
            if not active and not ready:
                readiness = db.readiness_report()
                backoff_deadline = _spawn_backoff_deadline(readiness)
                cooling_down = (
                    decision["slots"]
                    and backoff_deadline is not None
                    and backoff_deadline > db.clock()
                )
                if cooling_down:
                    # Give planning one opportunity for each changed durable state,
                    # then keep waiting without repeatedly spending planner calls.
                    revision = db.quiescence_snapshot()["revision"]
                    token = _spawn_backoff_plan_token(readiness, revision)
                    should_plan = token != backoff_plan_token
                    backoff_plan_token = token
                else:
                    backoff_plan_token = None
                    should_plan = True
                if decision["slots"] and department_plan_transport is not None:
                    from .department_plan_cycle import ensure_department_plans

                    if ensure_department_plans(
                        state,
                        db,
                        department_plan_transport,
                        stop_guard=lambda: not (state / "STOP").exists(),
                    ):
                        continue
                from .planning import plan_next

                if decision["slots"] and should_plan and plan_next(state, db):
                    continue
                if decision["slots"] and contract_review_transport is not None:
                    from .contract_review_cycle import process_pending_reviews

                    review_cycle = process_pending_reviews(
                        db,
                        contract_review_transport,
                        limit=1,
                        allowed_request_ids=allowed_contract_reviews,
                        dispatch_guard=lambda request: _contract_review_dispatch_guard(
                            state, db, cfg, request
                        ),
                    )
                    if review_cycle["processed"]:
                        activate_contract(state, db)
                        log_event(
                            state,
                            "contract_reviews_processed",
                            processed=review_cycle["processed"],
                            failures=review_cycle["failures"],
                            transport_invocations=review_cycle["transport_invocations"],
                            dispatch_authority=review_cycle["dispatch_authority"],
                        )
                        continue
                if cooling_down:
                    wait_seconds = min(2.0, max(0.0, backoff_deadline - db.clock()))
                    if wait_seconds:
                        time.sleep(wait_seconds)
                    continue
                snapshot = db.quiescence_snapshot()
                readiness = snapshot["readiness"]
                if any(
                    row["state"] == "running"
                    or (row["state"] == "submitted" and row["task_id"] not in blocked_verification)
                    for row in readiness
                ):
                    # The earlier active/ready reads may have gone stale while the
                    # supervisor reconciled and planned. Let the next pass act on the
                    # fresh state instead of recording a quiescence handoff for it.
                    time.sleep(2)
                    continue
                decision = choose_capacity(
                    measured_usage(state, db),
                    {
                        "available_memory_bytes": available_memory(),
                        "cpu_count": os.cpu_count(),
                        "active_team_ids": [],
                    },
                    cfg["resources"],
                )
                fresh_dispatchable_ids = set()
                fresh_assignment_blocked = {}
                for row in readiness:
                    if not row["eligible"]:
                        continue
                    try:
                        current_task = db.get_task(row["task_id"])
                        require_controller_assignment(cfg, db, current_task)
                    except StateError as exc:
                        fresh_assignment_blocked[row["task_id"]] = str(exc)
                        continue
                    fresh_dispatchable_ids.add(row["task_id"])
                if decision["slots"] and fresh_dispatchable_ids:
                    # Capacity and task readiness were observed separately; avoid
                    # classifying ready work as dependency-blocked after capacity changed.
                    time.sleep(2)
                    continue
                resource_reason = decision.get("reason", "unknown")
                if decision["slots"] == 0:
                    reason = "local-capacity-unavailable"
                elif any(
                    row["state"] == "pending"
                    and (
                        row["task_id"] in fresh_assignment_blocked
                        or any(
                            blocker.get("kind") == "controller_assignment"
                            for blocker in row["blockers"]
                        )
                    )
                    for row in readiness
                ):
                    reason = "controller-assignment-required"
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
                if (state / "STOP").exists():
                    break
                try:
                    require_controller_assignment(cfg, db, task)
                except StateError as exc:
                    log_event(
                        state,
                        "attempt_dispatch_blocked",
                        task=task["id"],
                        reason=str(exc),
                    )
                    continue
                attempt = db.claim(task["id"], LUNA, LEASE)
                if not attempt:
                    continue
                log = (state / "attempt-output.log").open("ab")
                if (state / "STOP").exists():
                    log.close()
                    db.cancel_unstarted_claim(
                        attempt["id"], attempt["epoch"], reason="stop_before_spawn"
                    )
                    break
                try:
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
                except OSError as exc:
                    log.close()
                    db.cancel_unstarted_claim(
                        attempt["id"], attempt["epoch"], reason="popen_failed"
                    )
                    log_event(
                        state,
                        "attempt_spawn_failed",
                        attempt_id=attempt["id"],
                        task=task["id"],
                        error_type=type(exc).__name__,
                    )
                    continue
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
        if (state / "STOP").exists():
            exit_reason = "stop_requested"
        elif (db.company_control_state() or {}).get("phase") == "draining":
            exit_reason = "company_hibernation"
        elif time.monotonic() >= end:
            exit_reason = "episode_deadline"
        else:
            exit_reason = "quiescent"
        checkpoint_snapshot = db.capture_supervisor_episode_snapshot(state)
        checkpoint = db.record_supervisor_episode_checkpoint(
            state, checkpoint_snapshot, exit_reason=exit_reason
        )
        log_event(
            state,
            "supervisor_episode_checkpointed",
            checkpoint_id=checkpoint["body"]["checkpoint_id"],
            exit_reason=exit_reason,
        )
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
