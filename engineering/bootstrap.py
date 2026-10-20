"""Create-only bounded offline Brain organization deployment."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .confinement import qualify_sandbox
from .org_state import Store
from .qualification import qualify_native_profile
from .resources import DEFAULT_POLICY
from .supervisor import atomic, git
from .usage_ledger import UsageLedger


def bootstrap(state: Path, source: Path, python: str):
    state = state.resolve()
    if any(
        (state / name).exists()
        for name in ("config.json", "organization.sqlite", "usage-ledger.sqlite")
    ):
        raise ValueError("deployment_already_exists")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    for name in ("trusted", "intents", "attempts", "receipts", "verification", "controller"):
        (state / name).mkdir(exist_ok=True)
    qualification = qualify_sandbox(
        state, python_executable=python, read_roots=["/opt/homebrew/opt/sqlite"]
    )
    atomic(state / "confinement.json", qualification)
    if not qualification["qualified"]:
        raise RuntimeError("OS_confinement_not_qualified")
    (state / "native-qualification").mkdir(exist_ok=True)
    native_qualification = qualify_native_profile(
        state / "native-qualification", evidence_path=state / "native-profile.json"
    )
    if not native_qualification["qualified"]:
        raise RuntimeError("Codex_native_profile_not_qualified")
    code = Path(__file__).resolve().parent
    shutil.copytree(
        code, state / "controller/engineering", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copyfile(code / "pilot_acceptance.py", state / "trusted/pilot_acceptance.py")
    git(state, "clone", "--no-local", "--no-hardlinks", str(source), str(state / "integration"))
    base = git(source, "rev-parse", "HEAD")
    old_active = git(source, "rev-parse", "refs/remotes/origin/research/active")
    repo = state / "integration"
    git(repo, "checkout", "--detach", base)
    git(repo, "update-ref", "refs/heads/research/active", base)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/research/active")
    git(
        repo,
        "update-ref",
        "refs/tags/archive/research-active-pre-org-20261019",
        old_active,
        "0" * 40,
    )
    pytest = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    contracts = {
        "schema-validation": {
            "version": 2,
            "producer": "Experience",
            "consumer": "Platform",
            "interface": "memecoin_experience.schema.validate(schema)",
            "valid_behavior": (
                "Return original valid v1/v2/v3 schema; do not mutate input or alter "
                "admission/temporal semantics."
            ),
            "invalid_behavior": (
                "Every malformed JSON-like schema is rejected with ValueError, not "
                "AttributeError, KeyError or TypeError. Existing specific ValueError "
                "codes remain meaningful; generic malformed shape uses experience_schema "
                "or experience_schema_columns."
            ),
            "compatibility": (
                "Additive error-surface stabilization; no schema IDs, dataset hashes, "
                "valid records or scientific rules change."
            ),
        }
    }
    tasks = {
        "EXP-001": {
            "team": "Experience",
            "title": "Reject malformed representation schemas consistently",
            "brief": (
                "Harden src/memecoin_experience/schema.py validate: currently "
                "validate(None), list input, representation=None and nonmapping columns "
                "leak incidental exceptions. Implement stable ValueError rejection for "
                "malformed JSON-like input, including malformed v1 context and v3 fields. "
                "Preserve every valid v1/v2/v3 contract and existing specific ValueError "
                "codes, no mutation. Add focused tests in "
                "tests/test_experience_schema_validation.py (new). No checked_samples, "
                "membership, data, training or protected semantic changes. Contract "
                "proposal schema-validation v2 affects Platform's future CLI; acknowledge "
                "or reject explicitly."
            ),
            "scope": [
                "src/memecoin_experience/schema.py",
                "tests/test_experience_schema_validation.py",
            ],
            "checks": [
                pytest + ["tests/test_experience_schema_validation.py"],
                pytest + ["tests/test_experience_schema.py", "-k", "not training"],
            ],
            "dependencies": [],
            "contracts": {},
        },
        "CTX-001": {
            "team": "Platform",
            "title": "Prevent context packet reads through symlink sources",
            "brief": (
                "Harden tools/agent_context.py so _read, _query_snippets and build_packet "
                "never read or hash symlinked sources, including symlinked ancestors and "
                "symlinks pointing inside repo. Explicit ValueError rejection or missing "
                "diagnostic is acceptable. Ordinary source output semantics and mandatory "
                "budget rules preserved. Add meaningful regression tests in "
                "tests/test_agent_context.py. This work is independent of Experience's "
                "pending schema-validation v2 contract. As Platform Sol lead also "
                "review/ack that proposed public error contract for the later offline "
                "schema CLI; do not block this unrelated implementation on producer progress."
            ),
            "scope": ["tools/agent_context.py", "tests/test_agent_context.py"],
            "checks": [pytest + ["tests/test_agent_context.py"]],
            "dependencies": [],
            "contracts": {},
        },
        "PLAT-001": {
            "team": "Platform",
            "title": "Add offline representation schema validation CLI",
            "brief": (
                "After Experience schema-validation v2 integration, add "
                "tools/brain_schema_check.py and tests/test_brain_schema_check.py. "
                "Standalone invocation from repository checkout: "
                "python tools/brain_schema_check.py PATH. Read one explicitly provided "
                "UTF-8 JSON schema file; use existing memecoin_experience.schema.validate; "
                "do not read datasets, fit models, network or write files. stdout exactly "
                "one JSON object {schema:'brain-schema-check-v1',valid:boolean,"
                "error:string|null}; valid exit0 error null, malformed JSON/schema/"
                "unreadable input exit2 and stable nonempty error code without echoing "
                "file contents. Support normal python invocation without installed package "
                "by adding repo/src path. Tests pure synthetic including v1/v2/v3 and "
                "malformed inputs. No changes to producer or controller."
            ),
            "scope": ["tools/brain_schema_check.py", "tests/test_brain_schema_check.py"],
            "checks": [
                pytest
                + ["tests/test_brain_schema_check.py", "tests/test_experience_schema_validation.py"]
            ],
            "dependencies": ["EXP-001"],
            "contracts": {"schema-validation": 2},
        },
        "PLAT-002": {
            "team": "Platform",
            "title": "Reject ambiguous and nonstandard JSON in offline schema checks",
            "brief": (
                "Improve tools/brain_schema_check.py to reject duplicate JSON object keys "
                "at any nesting level and nonstandard NaN/Infinity/-Infinity constants, "
                "including otherwise ignored metadata. Preserve valid v1/v2/v3 results "
                "and output shape/exit codes. Error messages must be stable codes and "
                "not disclose input contents. Add synthetic regression tests to "
                "tests/test_brain_schema_check.py. No dataset/runtime/training work."
            ),
            "scope": ["tools/brain_schema_check.py", "tests/test_brain_schema_check.py"],
            "checks": [pytest + ["tests/test_brain_schema_check.py"]],
            "dependencies": ["PLAT-001"],
            "contracts": {"schema-validation": 2},
        },
    }
    cfg = {
        "schema": "brain-engineering-deployment-v1",
        "python": python,
        "codex_executable": str(Path(shutil.which("codex")).resolve(strict=True)),
        "codex_executable_sha256": native_qualification["executable_sha256"],
        "controller": str(state / "controller"),
        "base": base,
        "old_active": old_active,
        "main_pin": git(source, "rev-parse", "refs/remotes/origin/main"),
        "tasks": tasks,
        "seed_tasks": ["EXP-001", "CTX-001", "PLAT-001"],
        "catalogue": ["PLAT-002"],
        "contracts": contracts,
        "resources": DEFAULT_POLICY,
        "remote_publication_enabled": False,
    }
    atomic(state / "config.json", cfg)
    (state / "config.sha256").write_text(
        hashlib.sha256((state / "config.json").read_bytes()).hexdigest()
    )
    manifest = {
        str(p.relative_to(state / "controller")): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (state / "controller").rglob("*.py")
    }
    manifest["trusted/pilot_acceptance.py"] = hashlib.sha256(
        (state / "trusted/pilot_acceptance.py").read_bytes()
    ).hexdigest()
    atomic(state / "controller-manifest.json", manifest)
    ledger = UsageLedger(state / "usage-ledger.sqlite")
    ledger.close()
    db = Store(state / "organization.sqlite")
    db.add_objective(
        "offline-brain-engineering",
        "Improve Brain offline validation and developer safety",
        "Bounded code-only objective; no acquisition, training, runtime or main changes.",
    )
    db.put_contract(
        "schema-validation",
        1,
        {
            "interface": contracts["schema-validation"]["interface"],
            "limitation": "Malformed JSON-like schemas can raise incidental exceptions.",
        },
    )
    db.put_decision(
        "owner-authority",
        {
            "scope": "Astra-directed offline engineering within pinned objective envelopes",
            "executive": {"model": "gpt-6-astra", "effort": "high", "episodic": True},
            "main": "unchanged",
            "publication": "local gates then research/active only",
            "models": ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"],
        },
    )
    for name, task in tasks.items():
        if name not in cfg["seed_tasks"]:
            continue
        db.add_task(
            name,
            task["team"],
            task["title"],
            task["brief"],
            scope=task["scope"],
            acceptance=[
                "Independent frozen acceptance",
                "Focused regression tests",
                "Independent Sol review",
            ],
            dependencies=task["dependencies"],
            contracts=task["contracts"],
            base_sha=base,
            objective_id="offline-brain-engineering",
            max_attempts=3,
        )
    db.propose_contract_transition(
        "schema-validation",
        1,
        contracts["schema-validation"]["version"],
        contracts["schema-validation"],
        producer_task_id="EXP-001",
        producer_team=contracts["schema-validation"]["producer"],
        consumer_teams=[contracts["schema-validation"]["consumer"]],
    )
    db.close()
    return cfg
