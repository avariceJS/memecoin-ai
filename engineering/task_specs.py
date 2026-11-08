"""Pure validation for trusted-envelope structured task proposals."""

from __future__ import annotations

import copy
import math
import re
from typing import Any

_SCHEMA = "brain-task-spec-v1"
_ENVELOPE_SCHEMA = "brain-task-envelope-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-[0-9]{3,8}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PATH_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
_SPEC_FIELDS = {
    "schema",
    "id",
    "objective_id",
    "team",
    "title",
    "brief",
    "base_sha",
    "scope",
    "dependencies",
    "contracts",
    "risk",
    "capabilities",
    "resources",
    "acceptance",
    "evidence_refs",
}
_RESOURCE_FIELDS = {"max_attempts", "max_turn_seconds", "estimated_wpu"}
_MAX_INT_BITS = 256
_EXISTING_TASK_STATES = {
    "pending",
    "running",
    "submitted",
    "verified",
    "accepted",
    "integrated",
    "failed",
}


def _fail(code: str) -> None:
    raise ValueError(code)


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 32:
        _fail("invalid_json_value")
    if value is None or type(value) in (str, bool):
        if isinstance(value, str) and len(value) > 4096:
            _fail("invalid_json_value")
        return
    if type(value) is int:
        if value.bit_length() > _MAX_INT_BITS:
            _fail("invalid_json_value")
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail("invalid_json_value")
        return
    if type(value) is list:
        if len(value) > 1024:
            _fail("invalid_json_value")
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict:
        if len(value) > 1024 or any(type(k) is not str or len(k) > 256 for k in value):
            _fail("invalid_json_value")
        for item in value.values():
            _json_value(item, depth + 1)
        return
    _fail("invalid_json_value")


def _string(value: Any, code: str, limit: int, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value) or len(value) > limit:
        _fail(code)
    return value


def _path(value: Any) -> str:
    path = _string(value, "invalid_scope", 240)
    parts = path.split("/")
    if (
        path.startswith("/")
        or "\\" in path
        or any(p in ("", ".", "..") or not _PATH_PART.fullmatch(p) for p in parts)
        or any(p.lower() in (".git", ".codex") for p in parts)
    ):
        _fail("invalid_scope")
    return path


def _exact_dict(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _contract_map(value: Any, code: str) -> dict[str, dict[str, Any]]:
    if type(value) is not dict or not 1 <= len(value) <= 32:
        _fail(code)
    for name, contract in value.items():
        if (
            type(name) is not str
            or not _NAME.fullmatch(name)
            or type(contract) is not dict
            or set(contract) != {"version", "proposal_sha256"}
        ):
            _fail(code)
        version = contract["version"]
        digest = contract["proposal_sha256"]
        if (
            type(version) is not int
            or version < 1
            or type(digest) is not str
            or not _SHA256.fullmatch(digest)
        ):
            _fail(code)
    return value


def _envelope_list(value: Any) -> list[str]:
    if (
        type(value) is not list
        or not value
        or len(value) > 256
        or any(type(item) is not str for item in value)
        or len(set(value)) != len(value)
    ):
        _fail("invalid_envelope")
    return value


def validate_task_spec(
    spec: Any, *, envelope: Any, acceptance_registry: Any, existing_tasks: Any
) -> dict[str, Any]:
    """Validate and return an isolated canonical JSON-data copy of a proposal."""
    _json_value(spec)
    _json_value(envelope)
    _json_value(acceptance_registry)
    _json_value(existing_tasks)
    s = _exact_dict(spec, _SPEC_FIELDS, "invalid_spec_fields")
    if s["schema"] != _SCHEMA:
        _fail("unsupported_schema")
    task_id = _string(s["id"], "invalid_id", 20)
    if not _ID.fullmatch(task_id):
        _fail("invalid_id")
    objective = _string(s["objective_id"], "invalid_objective", 64)
    team = _string(s["team"], "invalid_team", 64)
    _string(s["title"], "invalid_title", 160)
    _string(s["brief"], "invalid_brief", 2000)
    base = _string(s["base_sha"], "invalid_base", 40)
    if not _BASE.fullmatch(base):
        _fail("invalid_base")
    risk = _string(s["risk"], "invalid_risk", 32)

    e = _exact_dict(
        envelope,
        {
            "schema",
            "objective_id",
            "teams",
            "allowed_paths",
            "capabilities",
            "max_attempts",
            "max_turn_seconds",
            "allowed_contracts",
            "base_sha",
            "risk_levels",
        },
        "invalid_envelope",
    )
    for key in ("schema", "objective_id", "base_sha"):
        if type(e[key]) is not str:
            _fail("invalid_envelope")
    for key in ("teams", "allowed_paths", "capabilities", "risk_levels"):
        _envelope_list(e[key])
    allowed_contracts = e["allowed_contracts"]
    if type(allowed_contracts) is not dict:
        _fail("invalid_envelope")
    max_attempts = e["max_attempts"]
    max_seconds = e["max_turn_seconds"]
    if (
        type(max_attempts) is not int
        or max_attempts < 1
        or type(max_seconds) is not int
        or max_seconds < 1
    ):
        _fail("invalid_envelope")
    if (
        e["schema"] != _ENVELOPE_SCHEMA
        or e["objective_id"] != objective
        or team not in e["teams"]
        or base != e["base_sha"]
        or risk not in e["risk_levels"]
    ):
        _fail("outside_envelope")
    scope = s["scope"]
    if type(scope) is not list or not 1 <= len(scope) <= 64:
        _fail("invalid_scope")
    scope = [_path(p) for p in scope]
    if len(set(scope)) != len(scope) or not set(scope) <= set(e["allowed_paths"]):
        _fail("scope_outside_envelope")
    caps = s["capabilities"]
    if (
        type(caps) is not list
        or not caps
        or len(caps) > 32
        or any(type(c) is not str for c in caps)
        or len(set(caps)) != len(caps)
        or not set(caps) <= set(e["capabilities"])
    ):
        _fail("unsupported_capability")

    contracts = s["contracts"]
    if type(contracts) is not dict or not 1 <= len(contracts) <= 32:
        _fail("invalid_contracts")
    _contract_map(contracts, "invalid_contracts")
    for name, contract in contracts.items():
        version = contract["version"]
        digest = contract["proposal_sha256"]
        if (
            type(version) is not int
            or version < 1
            or type(digest) is not str
            or not _SHA256.fullmatch(digest)
        ):
            _fail("invalid_contracts")
        versions = allowed_contracts.get(name)
        if type(versions) is not dict or versions.get(str(version)) != digest:
            _fail("contract_outside_envelope")

    resources = _exact_dict(s["resources"], _RESOURCE_FIELDS, "invalid_resources")
    attempts, seconds, wpu = (
        resources["max_attempts"],
        resources["max_turn_seconds"],
        resources["estimated_wpu"],
    )
    if type(attempts) is not int or not 1 <= attempts <= max_attempts:
        _fail("invalid_resources")
    if type(seconds) is not int or not 1 <= seconds <= max_seconds:
        _fail("invalid_resources")
    if (
        type(wpu) not in (int, float)
        or (type(wpu) is float and not math.isfinite(wpu))
        or not 0 <= wpu <= 100000
    ):
        _fail("invalid_resources")

    acceptance = s["acceptance"]
    registry = acceptance_registry
    if (
        type(registry) is not dict
        or not 1 <= len(registry) <= 128
        or type(acceptance) is not list
        or not 1 <= len(acceptance) <= 128
    ):
        _fail("invalid_acceptance")
    seen: set[str] = set()
    for item in acceptance:
        item = _exact_dict(item, {"id", "sha256"}, "invalid_acceptance")
        aid, digest = item["id"], item["sha256"]
        if (
            type(aid) is not str
            or not _NAME.fullmatch(aid)
            or aid in seen
            or type(digest) is not str
            or not _SHA256.fullmatch(digest)
        ):
            _fail("invalid_acceptance")
        seen.add(aid)
        record = registry.get(aid)
        if type(record) is not dict or set(record) != {
            "sha256",
            "objective_id",
            "scope",
            "contracts",
            "independent",
            "frozen",
            "risk",
        }:
            _fail("untrusted_acceptance")
        record_contracts = _contract_map(record["contracts"], "untrusted_acceptance")
        if (
            type(record["sha256"]) is not str
            or not _SHA256.fullmatch(record["sha256"])
            or type(record["objective_id"]) is not str
            or type(record["risk"]) is not str
            or type(record["independent"]) is not bool
            or type(record["frozen"]) is not bool
            or record["independent"] is not True
            or record["frozen"] is not True
            or record["sha256"] != digest
            or record["objective_id"] != objective
            or record["risk"] != risk
            or record_contracts != contracts
        ):
            _fail("acceptance_mismatch")
        record_scope = record["scope"]
        if (
            type(record_scope) is not list
            or any(type(path) is not str for path in record_scope)
            or len(set(record_scope)) != len(record_scope)
            or set(record_scope) != set(scope)
        ):
            _fail("acceptance_scope_mismatch")

    refs = s["evidence_refs"]
    if (
        type(refs) is not list
        or not 1 <= len(refs) <= 64
        or any(type(ref) is not str or not ref or len(ref) > 240 for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        _fail("invalid_evidence_refs")
    deps = s["dependencies"]
    if (
        type(deps) is not list
        or len(deps) > 64
        or any(type(dep) is not str or not _ID.fullmatch(dep) for dep in deps)
        or len(set(deps)) != len(deps)
    ):
        _fail("invalid_dependencies")
    if task_id in deps:
        _fail("self_dependency")
    if type(existing_tasks) is not list or len(existing_tasks) > 10000:
        _fail("invalid_existing_tasks")
    by_id: dict[str, dict[str, Any]] = {}
    for row in existing_tasks:
        if (
            type(row) is not dict
            or type(row.get("id")) is not str
            or type(row.get("state")) is not str
            or type(row.get("scope")) is not list
            or len(row.get("scope", [])) > 64
        ):
            _fail("invalid_existing_tasks")
        rid = row["id"]
        if not _ID.fullmatch(rid):
            _fail("invalid_existing_tasks")
        if row["state"] not in _EXISTING_TASK_STATES:
            _fail("invalid_existing_tasks")
        row_scope = [_path(path) for path in row["scope"]]
        if len(set(row_scope)) != len(row_scope):
            _fail("invalid_existing_tasks")
        if rid in by_id:
            _fail("duplicate_existing_id")
        by_id[rid] = row
        if rid == task_id:
            _fail("duplicate_task_id")
        if row["state"] in {"running", "submitted", "verified", "accepted"}:
            if set(scope) & set(row_scope):
                _fail("owned_scope_overlap")
    for dep in deps:
        if dep not in by_id:
            _fail("unknown_dependency")
    graph: dict[str, list[str]] = {}
    for rid, row in by_id.items():
        row_deps = row.get("dependencies", [])
        if (
            type(row_deps) is not list
            or len(row_deps) > 64
            or any(type(dep) is not str or not _ID.fullmatch(dep) for dep in row_deps)
            or len(set(row_deps)) != len(row_deps)
        ):
            _fail("invalid_existing_tasks")
        graph[rid] = row_deps
    graph[task_id] = deps
    visited: set[str] = set()
    active: set[str] = set()
    for start in graph:
        if start in visited:
            continue
        active.add(start)
        stack = [(start, iter(graph[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                active.remove(node)
                visited.add(node)
                continue
            if child not in graph:
                _fail("unknown_dependency")
            if child in active:
                _fail("dependency_cycle")
            if child not in visited:
                active.add(child)
                stack.append((child, iter(graph[child])))
    return copy.deepcopy(s)
