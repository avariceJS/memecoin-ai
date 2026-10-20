"""Controller-owned immutable task and verifier bindings.

Task proposals name frozen acceptance artifacts; they never provide executable checks.
This module turns validated task metadata plus a controller-owned verifier registry into a
content-addressed binding suitable for persistence alongside the admission decision.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .confinement import VERIFIER_ENV_KEYS


class ExecutionBindingError(ValueError):
    """Missing, malformed, or drifted trusted task execution binding."""


_HASH = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ExecutionBindingError("invalid_execution_binding_json") from None


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _same(left: Any, right: Any) -> bool:
    return _canonical(left) == _canonical(right)


def _hash(value: Any, code: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ExecutionBindingError(code)
    return value


def build_execution_binding(
    spec: Any,
    *,
    verifier_registry: Any,
    controller_release_sha256: str,
    integration_policy_sha256: str,
) -> dict:
    """Build a detached binding from trusted registry records, never proposal commands."""
    _hash(controller_release_sha256, "invalid_controller_release_hash")
    _hash(integration_policy_sha256, "invalid_integration_policy_hash")
    if type(spec) is not dict or type(spec.get("acceptance")) is not list:
        raise ExecutionBindingError("invalid_execution_binding_spec")
    if type(verifier_registry) is not dict:
        raise ExecutionBindingError("invalid_verifier_registry")

    verifiers = []
    for acceptance in spec["acceptance"]:
        if type(acceptance) is not dict or set(acceptance) != {"id", "sha256"}:
            raise ExecutionBindingError("invalid_execution_binding_acceptance")
        acceptance_id = acceptance["id"]
        acceptance_sha = _hash(acceptance["sha256"], "invalid_acceptance_hash")
        record = verifier_registry.get(acceptance_id)
        fields = {
            "acceptance_sha256",
            "policy_id",
            "commands",
            "env",
            "harness_sha256",
            "result_schema",
            "case_field",
            "passed_field",
        }
        if type(record) is not dict or set(record) != fields:
            raise ExecutionBindingError("untrusted_verifier_record")
        if _hash(record["acceptance_sha256"], "invalid_verifier_acceptance_hash") != acceptance_sha:
            raise ExecutionBindingError("verifier_acceptance_hash_mismatch")
        _hash(record["harness_sha256"], "invalid_verifier_harness_hash")
        if type(record["policy_id"]) is not str or not record["policy_id"].strip():
            raise ExecutionBindingError("invalid_verifier_policy_id")
        commands = record["commands"]
        if type(commands) is not list or not commands or len(commands) > 32:
            raise ExecutionBindingError("invalid_verifier_commands")
        canonical_commands = []
        for argv in commands:
            if type(argv) is not list or not argv or len(argv) > 100:
                raise ExecutionBindingError("invalid_verifier_commands")
            if any(type(arg) is not str or not arg or "\x00" in arg for arg in argv):
                raise ExecutionBindingError("invalid_verifier_commands")
            if any(
                ("{" in arg or "}" in arg)
                and arg not in {"{checkout}", "{task_id}", "{harness}", "{interpreter}"}
                for arg in argv
            ):
                raise ExecutionBindingError("invalid_verifier_placeholder")
            canonical_commands.append(list(argv))
        env = record["env"]
        if type(env) is not dict or any(
            type(key) is not str or not key or type(value) is not str or "\x00" in key + value
            for key, value in env.items()
        ):
            raise ExecutionBindingError("invalid_verifier_environment")
        if set(env) - VERIFIER_ENV_KEYS:
            raise ExecutionBindingError("invalid_verifier_environment")
        if (
            type(record["result_schema"]) is not str
            or not record["result_schema"].strip()
            or type(record["case_field"]) is not str
            or not record["case_field"].strip()
            or type(record["passed_field"]) is not str
            or not record["passed_field"].strip()
        ):
            raise ExecutionBindingError("invalid_verifier_result_contract")
        verifiers.append(
            {
                "acceptance_id": acceptance_id,
                "acceptance_sha256": acceptance_sha,
                "policy_id": record["policy_id"],
                "commands": canonical_commands,
                "env": dict(sorted(env.items())),
                "harness_sha256": record["harness_sha256"],
                "result_schema": record["result_schema"],
                "case_field": record["case_field"],
                "passed_field": record["passed_field"],
            }
        )
    body = {
        "schema": "brain-task-execution-binding-v1",
        "task_id": spec.get("id"),
        "spec_sha256": _digest(spec),
        "objective_id": spec.get("objective_id"),
        "team": spec.get("team"),
        "base_sha": spec.get("base_sha"),
        "scope": list(spec.get("scope", [])),
        "capabilities": list(spec.get("capabilities", [])),
        "resources": dict(spec.get("resources", {})),
        "dependencies": list(spec.get("dependencies", [])),
        "contracts": spec.get("contracts"),
        "integration_policy_sha256": integration_policy_sha256,
        "controller_release_sha256": controller_release_sha256,
        "verifiers": verifiers,
    }
    body["binding_sha256"] = _digest(body)
    return json.loads(_canonical(body))


def resolve_execution_binding(
    task_id: str,
    task: Any,
    decision: Any,
    *,
    controller_release_sha256: str,
) -> dict:
    """Resolve one immutable admission decision and reject task or controller drift."""
    _hash(controller_release_sha256, "invalid_controller_release_hash")
    if type(task) is not dict or type(decision) is not dict:
        raise ExecutionBindingError("missing_execution_binding")
    body = decision.get("body")
    if type(body) is not dict or type(body.get("spec")) is not dict:
        raise ExecutionBindingError("missing_execution_binding")
    binding = body.get("execution_binding")
    if type(binding) is not dict:
        raise ExecutionBindingError("missing_execution_binding")
    binding_fields = {
        "schema",
        "task_id",
        "spec_sha256",
        "objective_id",
        "team",
        "base_sha",
        "scope",
        "capabilities",
        "resources",
        "dependencies",
        "contracts",
        "integration_policy_sha256",
        "controller_release_sha256",
        "verifiers",
        "binding_sha256",
    }
    if set(binding) != binding_fields or binding.get("schema") != "brain-task-execution-binding-v1":
        raise ExecutionBindingError("invalid_execution_binding_shape")
    if decision.get("name") != "admission/task-spec/" + task_id or decision.get("version") != 1:
        raise ExecutionBindingError("execution_decision_identity_mismatch")
    if body["spec"].get("id") != task_id or binding.get("task_id") != task_id:
        raise ExecutionBindingError("execution_task_identity_mismatch")
    if binding.get("controller_release_sha256") != controller_release_sha256:
        raise ExecutionBindingError("execution_controller_release_mismatch")
    expected_hash = binding.get("binding_sha256")
    without_hash = {key: value for key, value in binding.items() if key != "binding_sha256"}
    if _hash(expected_hash, "invalid_execution_binding_hash") != _digest(without_hash):
        raise ExecutionBindingError("execution_binding_digest_mismatch")
    if binding.get("spec_sha256") != _digest(body["spec"]):
        raise ExecutionBindingError("execution_spec_digest_mismatch")
    spec = body["spec"]
    binding_projection = {
        key: binding.get(key)
        for key in (
            "objective_id",
            "team",
            "base_sha",
            "scope",
            "capabilities",
            "resources",
            "dependencies",
            "contracts",
        )
    }
    spec_projection = {
        key: spec.get(key)
        for key in (
            "objective_id",
            "team",
            "base_sha",
            "scope",
            "capabilities",
            "resources",
            "dependencies",
            "contracts",
        )
    }
    if not _same(binding_projection, spec_projection):
        raise ExecutionBindingError("execution_spec_binding_mismatch")
    if type(binding.get("verifiers")) is not list or not binding["verifiers"]:
        raise ExecutionBindingError("invalid_execution_binding_verifiers")
    expected = {
        "team": binding.get("team"),
        "title": body["spec"].get("title"),
        "brief": body["spec"].get("brief"),
        "scope": body["spec"].get("scope"),
        "base_sha": binding.get("base_sha"),
        "objective_id": binding.get("objective_id"),
        "max_attempts": binding.get("resources", {}).get("max_attempts"),
        "dependencies": spec.get("dependencies"),
        "contracts": {
            name: requirement["version"] for name, requirement in spec.get("contracts", {}).items()
        },
        "acceptance": [f"{item['id']}:{item['sha256']}" for item in spec.get("acceptance", [])],
    }
    actual = {key: task.get(key) for key in expected}
    if (
        not _same(actual, expected)
        or not _same(binding.get("scope"), spec.get("scope"))
        or not _same(binding.get("dependencies"), spec.get("dependencies"))
        or not _same(binding.get("contracts"), spec.get("contracts"))
        or any(
            type(item) is not dict
            or type(item.get("acceptance_id")) is not str
            or type(item.get("acceptance_sha256")) is not str
            for item in binding["verifiers"]
        )
        or [f"{item['acceptance_id']}:{item['acceptance_sha256']}" for item in binding["verifiers"]]
        != expected["acceptance"]
    ):
        raise ExecutionBindingError("execution_task_binding_mismatch")
    return json.loads(_canonical(binding))
