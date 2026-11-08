"""Build one controller-owned verifier plan for verification and recovery.

This module prepares identities and policy inputs only. It does not execute commands, publish Git
refs, or confer runtime authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .git_gate import VerificationPolicy
from .org_state import StateError, Store
from .verifier_manifest import (
    VerifierManifestError,
    load_verifier_manifest,
    resolve_verifier_manifest,
)


class VerificationPlanError(ValueError):
    """A verifier plan is missing or inconsistent with its trusted source."""


@dataclass(frozen=True)
class BoundCommand:
    executable_path: Path
    executable_sha256: str
    runtime_path: Path
    runtime_cdhash: str
    script_path: Path | None
    script_sha256: str | None
    result: Mapping[str, str]

    def __post_init__(self) -> None:
        result = dict(self.result)
        if any(type(key) is not str or type(value) is not str for key, value in result.items()):
            raise ValueError("bound_command_result_invalid")
        object.__setattr__(self, "result", MappingProxyType(result))


@dataclass(frozen=True)
class BoundVerifier:
    acceptance_id: str
    commands: tuple[tuple[str, ...], ...]
    harness_path: Path
    interpreter_path: Path
    interpreter_sha256: str
    harness_sha256: str
    interpreter_runtime_path: Path
    interpreter_runtime_cdhash: str
    result_schema: str
    case_field: str
    passed_field: str
    command_bindings: tuple[BoundCommand, ...]


@dataclass(frozen=True)
class VerificationPlan:
    policy: VerificationPolicy
    execution_qualified: bool
    reason: str | None
    binding_sha256: str | None
    decision_name: str | None
    decision_version: int | None
    acceptance_identities: tuple[str, ...]
    plan_sha256: str
    verifier_manifest_sha256: str | None = None
    bound_verifiers: tuple[BoundVerifier, ...] = ()
    interpreter_path: str | None = None
    interpreter_sha256: str | None = None
    interpreter_runtime_path: str | None = None
    interpreter_runtime_cdhash: str | None = None

    def identity(self) -> dict[str, Any]:
        """Return the JSON-safe identity that must travel with an admitted attempt."""
        identity = {
            "plan_sha256": self.plan_sha256,
            "policy_sha256": self.policy.digest,
            "binding_sha256": self.binding_sha256,
            "decision_name": self.decision_name,
            "decision_version": self.decision_version,
            "acceptance_identities": list(self.acceptance_identities),
        }
        if self.verifier_manifest_sha256 is not None:
            identity["verifier_manifest_sha256"] = self.verifier_manifest_sha256
            identity["interpreter_path"] = self.interpreter_path
            identity["interpreter_sha256"] = self.interpreter_sha256
            identity["interpreter_runtime_path"] = self.interpreter_runtime_path
            identity["interpreter_runtime_cdhash"] = self.interpreter_runtime_cdhash
        return identity


def attempt_plan_matches(plan: VerificationPlan, evidence: Any) -> tuple[bool, str | None]:
    """Require attempt-to-verifier identity equality, allowing absent legacy evidence only."""
    if type(evidence) is not dict:
        return False, "attempt_evidence_invalid"
    if "verification_plan" not in evidence:
        if plan.binding_sha256 is None:
            return True, None
        return False, "attempt_verification_plan_missing"
    recorded = evidence["verification_plan"]
    if type(recorded) is not dict:
        return False, "attempt_verification_plan_invalid"
    try:
        expected_bytes = json.dumps(
            plan.identity(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        recorded_bytes = json.dumps(
            recorded, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False, "attempt_verification_plan_invalid"
    if recorded_bytes != expected_bytes:
        return False, "attempt_verification_plan_mismatch"
    return True, None


def _digest(body: Any) -> str:
    try:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise VerificationPlanError("invalid_verification_plan") from None
    return hashlib.sha256(raw).hexdigest()


def expand_bound_verifier_command(
    command: tuple[str, ...],
    *,
    checkout: Path,
    task_id: str,
    harness_path: Path,
    interpreter_path: Path,
) -> tuple[str, ...]:
    """Expand only controller-approved placeholders in a manifest-bound command."""
    if type(command) is not tuple or any(type(arg) is not str for arg in command):
        raise VerificationPlanError("bound_verifier_command_invalid")
    substitutions = {
        "{checkout}": str(checkout),
        "{task_id}": task_id,
        "{harness}": str(harness_path),
        "{interpreter}": str(interpreter_path),
    }
    return tuple(substitutions.get(arg, arg) for arg in command)


def prepare_verification_plan(state: Path, db: Store, task: dict, cfg: dict) -> VerificationPlan:
    """Prepare a fixed-catalogue plan or an explicitly unqualified bound plan.

    A spec-backed task never falls back to the legacy `cfg['tasks']` catalogue. When deployment
    config pins `verifier_manifest_sha256`, the exact controller manifest and trusted harness bytes
    are resolved and the manifest digest joins the attempt identity. Executable qualification stays
    false until the running interpreter identity, loader closure, sandbox and result execution are
    qualified separately.
    """
    task_id = task.get("id") if type(task) is dict else None
    if type(task_id) is not str or not task_id:
        raise VerificationPlanError("invalid_verification_task")
    try:
        saved_spec = db.get_task_spec(task_id)
    except StateError as exc:
        raise VerificationPlanError(str(exc)) from None
    bound_verifiers: tuple[BoundVerifier, ...] = ()
    interpreter_path: str | None = None
    interpreter_sha256: str | None = None
    interpreter_runtime_path: str | None = None
    interpreter_runtime_cdhash: str | None = None
    if saved_spec is None:
        try:
            commands = [
                (cfg["python"], str(state / "trusted/pilot_acceptance.py"), task_id, "{checkout}")
            ]
            commands += [tuple(command) for command in cfg["tasks"][task_id]["checks"]]
        except (KeyError, TypeError):
            raise VerificationPlanError("legacy_verifier_policy_missing") from None
        policy = VerificationPolicy(tuple(commands), policy_id="brain-org-pilot-v1:" + task_id)
        decision_name = None
        decision_version = None
        binding_sha256 = None
        acceptance_identities: tuple[str, ...] = ()
        verifier_manifest_sha256 = None
        qualified, reason = True, None
    else:
        release_sha256 = cfg.get("controller_release_sha256")
        policy_sha256 = cfg.get("integration_policy_sha256")
        if type(release_sha256) is not str or type(policy_sha256) is not str:
            raise VerificationPlanError("bound_verifier_release_identity_missing")
        try:
            binding = db.resolve_task_execution_binding(
                task_id, controller_release_sha256=release_sha256
            )
        except (StateError, ValueError) as exc:
            raise VerificationPlanError(str(exc)) from None
        if binding.get("integration_policy_sha256") != policy_sha256:
            raise VerificationPlanError("bound_verifier_integration_policy_mismatch")
        if "verifier_manifest_sha256" not in cfg:
            verifier_manifest_sha256 = None
        else:
            configured_manifest_sha256 = cfg["verifier_manifest_sha256"]
            if (
                type(configured_manifest_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", configured_manifest_sha256) is None
            ):
                raise VerificationPlanError("bound_verifier_manifest_hash_invalid")
            configured_python = cfg.get("python")
            if (
                type(configured_python) is not str
                or not configured_python
                or not Path(configured_python).is_absolute()
            ):
                raise VerificationPlanError("bound_verifier_interpreter_config_missing")
            try:
                configured_interpreter_path = Path(configured_python).resolve(strict=True)
            except (OSError, ValueError, RuntimeError):
                raise VerificationPlanError(
                    "bound_verifier_interpreter_config_unavailable"
                ) from None
            manifest_path = state / "controller" / "verifier-manifest.json"
            try:
                manifest = load_verifier_manifest(
                    manifest_path, expected_sha256=configured_manifest_sha256
                )
                resolved = resolve_verifier_manifest(
                    binding,
                    manifest,
                    trusted_root=state / "trusted",
                    configured_interpreter=configured_interpreter_path,
                )
            except VerifierManifestError as exc:
                raise VerificationPlanError(str(exc)) from None
            verifier_manifest_sha256 = configured_manifest_sha256
            interpreter_path = resolved[0]["interpreter_path"]
            interpreter_sha256 = resolved[0]["interpreter_sha256"]
            interpreter_runtime_path = resolved[0]["interpreter_runtime_path"]
            interpreter_runtime_cdhash = resolved[0]["interpreter_runtime_cdhash"]
            bound_verifiers = tuple(
                BoundVerifier(
                    item["acceptance_id"],
                    tuple(tuple(command) for command in item["commands"]),
                    (state / "trusted" / item["harness_path"]).resolve(strict=True),
                    Path(item["interpreter_path"]),
                    item["interpreter_sha256"],
                    item["harness_sha256"],
                    Path(item["interpreter_runtime_path"]),
                    item["interpreter_runtime_cdhash"],
                    item["result_schema"],
                    item["case_field"],
                    item["passed_field"],
                    tuple(
                        BoundCommand(
                            Path(pin["executable_path"]),
                            pin["executable_sha256"],
                            Path(pin["runtime_path"]),
                            pin["runtime_cdhash"],
                            (state / "trusted" / pin["script_path"]).resolve(strict=True)
                            if pin["script_path"] is not None
                            else None,
                            pin["script_sha256"],
                            pin["result"],
                        )
                        for pin in item["command_bindings"]
                    ),
                )
                for item in resolved
            )
        verifiers = binding.get("verifiers")
        if type(verifiers) is not list or not verifiers:
            raise VerificationPlanError("bound_verifier_policy_missing")
        envs = [verifier.get("env") for verifier in verifiers if type(verifier) is dict]
        if len(envs) != len(verifiers) or any(type(env) is not dict for env in envs):
            raise VerificationPlanError("bound_verifier_environment_invalid")
        if any(
            type(key) is not str or not key or type(value) is not str or "\x00" in key + value
            for env in envs
            for key, value in env.items()
        ):
            raise VerificationPlanError("bound_verifier_environment_invalid")
        try:
            canonical_envs = {
                json.dumps(env, sort_keys=True, separators=(",", ":"), allow_nan=False)
                for env in envs
            }
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise VerificationPlanError("bound_verifier_environment_invalid") from None
        if len(canonical_envs) != 1:
            raise VerificationPlanError("bound_verifier_environment_mismatch")
        commands = []
        acceptance_ids = []
        for verifier in verifiers:
            acceptance_id = verifier.get("acceptance_id")
            acceptance_sha256 = verifier.get("acceptance_sha256")
            verifier_commands = verifier.get("commands")
            if (
                type(acceptance_id) is not str
                or not acceptance_id
                or type(acceptance_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", acceptance_sha256) is None
                or type(verifier_commands) is not list
                or not verifier_commands
            ):
                raise VerificationPlanError("bound_verifier_record_invalid")
            acceptance_ids.append(f"{acceptance_id}:{acceptance_sha256}")
            for command in verifier_commands:
                if (
                    type(command) is not list
                    or not command
                    or any(type(arg) is not str or not arg or "\x00" in arg for arg in command)
                ):
                    raise VerificationPlanError("bound_verifier_command_invalid")
                commands.append(tuple(command))
        policy = VerificationPolicy(
            tuple(commands),
            tuple(sorted(envs[0].items())),
            policy_id="brain-task-execution-binding-v1:" + binding["binding_sha256"],
        )
        decision_name = "admission/task-spec/" + task_id
        decision_version = saved_spec["decision"].get("version")
        binding_sha256 = binding["binding_sha256"]
        acceptance_identities = tuple(acceptance_ids)
        qualified = False
        reason = (
            "interpreter_or_sandbox_not_qualified"
            if verifier_manifest_sha256 is not None
            else "verifier_manifest_not_qualified"
        )

    plan_body = {
        "task_id": task_id,
        "policy_sha256": policy.digest,
        "execution_qualified": qualified,
        "reason": reason,
        "binding_sha256": binding_sha256,
        "decision_name": decision_name,
        "decision_version": decision_version,
        "acceptance_identities": acceptance_identities,
    }
    if verifier_manifest_sha256 is not None:
        plan_body["verifier_manifest_sha256"] = verifier_manifest_sha256
        plan_body["interpreter_path"] = interpreter_path
        plan_body["interpreter_sha256"] = interpreter_sha256
        plan_body["interpreter_runtime_path"] = interpreter_runtime_path
        plan_body["interpreter_runtime_cdhash"] = interpreter_runtime_cdhash
    return VerificationPlan(
        policy,
        qualified,
        reason,
        binding_sha256,
        decision_name,
        decision_version,
        acceptance_identities,
        _digest(plan_body),
        verifier_manifest_sha256,
        bound_verifiers,
        interpreter_path,
        interpreter_sha256,
        interpreter_runtime_path,
        interpreter_runtime_cdhash,
    )
