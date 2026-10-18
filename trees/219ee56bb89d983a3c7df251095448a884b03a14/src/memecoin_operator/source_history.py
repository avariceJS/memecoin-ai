"""Read-only source lineage for a report export. Not for a UI poll.

The scan is the current binding-checked source, the operator ``source_root`` when
it is a different directory, and one repair parent named by an authenticated
migration. Journal payloads contribute status tokens and counters only. This
module does not open a ledger lock, raw captures, or the network.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from memecoin_continuous.runtime import guard_source
from memecoin_operator.acquisition import current_source
from memecoin_registry.contracts import digest

SCHEMA = "source-lineage-v1"
MAX_FILES = 100_000
MAX_BYTES = 32 * 1024 * 1024
SILENT_GAP_NS = 120 * 1_000_000_000
MAX_GAPS = 16
MAX_INCIDENTS = 64
TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
INCIDENT_RE = re.compile(r"^incident-(\d+)\.json\Z")
PROVENANCE = "current_configured_original_repair_predecessor"


def snapshot(
    operator_root: Path | str,
    cutoff_ns: int,
    *,
    max_files: int = MAX_FILES,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Compact lineage at ``cutoff_ns``. Counters from separate roots are not added."""
    limits = _limits(max_files, max_bytes)
    if type(cutoff_ns) is not int or cutoff_ns < 0:
        return _fail("bad_cutoff", None, limits)
    if limits is None:
        return _fail("bad_limits", cutoff_ns, {"max_files": None, "max_bytes": None})
    root, reason = _operator_root(operator_root)
    if root is None:
        return _fail(reason or "bad_root", cutoff_ns, limits)
    meter = _Meter(max_files, max_bytes)
    try:
        current = current_source(root)
    except (OSError, ValueError) as exc:
        return _fail(_reason(exc), cutoff_ns, meter.public())
    if _blocked(current):
        return _fail(_block_reason(current), cutoff_ns, meter.public())
    config = _json_file(root / "config.json", meter)
    source_root = _configured_source(config)
    current_node = _source_node(current, "current", cutoff_ns, meter, provenance="current_source")
    original = _original_node(current, source_root, cutoff_ns, meter)
    repair = _repair_node(current_node, original, cutoff_ns, meter)
    body = {
        "ok": _ok(current_node, meter),
        "schema": SCHEMA,
        "cutoff_ns": cutoff_ns,
        "reason": None if _ok(current_node, meter) else _top_reason(current_node, meter),
        "network": False,
        "writes": False,
        "provenance_limit": PROVENANCE,
        "predecessor_hops": 1,
        "successor": _successor_status(root, current, current_node),
        "original_stop": _original_stop(current, original, current_node),
        "current": _public_node(current_node),
        "configured_original": _public_original(original),
        "repair_predecessor": None if repair is None else _public_node(repair),
        "budget": _budget(current_node, original, repair),
        "notices": _notices(current_node, original, repair),
        "limits": meter.public(),
    }
    return body


def _limits(max_files: int, max_bytes: int) -> dict[str, int] | None:
    if type(max_files) is not int or type(max_bytes) is not int:
        return None
    if max_files < 1 or max_files > MAX_FILES or max_bytes < 1 or max_bytes > MAX_BYTES:
        return None
    return {"max_files": max_files, "max_bytes": max_bytes}


def _fail(reason: str, cutoff: int | None, limits: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "schema": SCHEMA,
        "cutoff_ns": cutoff,
        "reason": reason,
        "network": False,
        "writes": False,
        "provenance_limit": PROVENANCE,
        "predecessor_hops": 1,
        "successor": None,
        "original_stop": None,
        "current": None,
        "configured_original": None,
        "repair_predecessor": None,
        "budget": _empty_budget(),
        "notices": [],
        "limits": limits,
    }


def _operator_root(raw: Path | str) -> tuple[Path | None, str | None]:
    if not isinstance(raw, (Path, str)):
        return None, "bad_root"
    path = Path(raw)
    if _protected(path):
        return None, "protected_root"
    if _symlink_component(path):
        return None, "symlink_root"
    if not path.is_dir():
        return None, "missing_root"
    return path, None


def _blocked(path: Path) -> bool:
    return _symlink_component(path) or _protected(path) or not path.is_dir()


def _block_reason(path: Path) -> str:
    if _symlink_component(path):
        return "symlink_source"
    if _protected(path):
        return "protected_source"
    return "missing_source"


def _configured_source(config: dict[str, Any] | None) -> Path | None:
    if not isinstance(config, dict):
        return None
    raw = config.get("source_root")
    if type(raw) is not str or not raw or "\x00" in raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    return path


def _source_node(
    path: Path,
    role: str,
    cutoff: int,
    meter: _Meter,
    *,
    provenance: str,
) -> dict[str, Any]:
    binding_ok, binding_reason, config = _binding(path)
    journal = (
        _journal(path, cutoff, meter)
        if binding_ok
        else _journal_body(present=False, chain_ok=None, unknown=True, reason=binding_reason)
    )
    node = {
        "role": role,
        "path": path,
        "provenance": provenance,
        "binding_ok": binding_ok,
        "binding_reason": binding_reason,
        "config": config if binding_ok else None,
        "stop": _stop(path, cutoff, meter),
        "incidents": _incidents(path, meter),
        "journal": journal,
        "health": _health(path, cutoff, meter) if binding_ok else None,
        "seed_attempts": _seed_attempts(path, meter) if binding_ok else None,
        "carry": _carry(path, config, meter) if binding_ok else None,
    }
    node["gaps"] = _gaps(node["journal"])
    return node


def _original_node(
    current: Path,
    source_root: Path | None,
    cutoff: int,
    meter: _Meter,
) -> dict[str, Any] | None:
    if source_root is None:
        return {"ok": False, "reason": "missing_source_root", "same_as_current": False}
    if _same(current, source_root):
        return {"ok": True, "same_as_current": True, "reason": None}
    if _blocked(source_root):
        return {
            "ok": False,
            "same_as_current": False,
            "reason": _block_reason(source_root),
            "node": None,
        }
    node = _source_node(
        source_root,
        "configured_original",
        cutoff,
        meter,
        provenance="operator_source_root",
    )
    return {"ok": True, "same_as_current": False, "reason": None, "node": node}


def _repair_node(
    current: dict[str, Any],
    original: dict[str, Any] | None,
    cutoff: int,
    meter: _Meter,
) -> dict[str, Any] | None:
    owner = current if current.get("carry") is not None else None
    if owner is None and isinstance(original, dict):
        nested = original.get("node")
        if isinstance(nested, dict) and nested.get("carry") is not None:
            owner = nested
    if owner is None:
        return None
    carry = owner["carry"]
    parent = carry.get("parent")
    if not isinstance(parent, Path):
        return None
    if _same(parent, owner["path"]):
        return None
    if isinstance(original, dict) and original.get("same_as_current") is False:
        nested = original.get("node")
        if isinstance(nested, dict) and _same(parent, nested["path"]):
            return None
    if _same(parent, current["path"]):
        return None
    if _blocked(parent):
        carry["refused"] = _block_reason(parent)
        return None
    if not (parent / "config.json").is_file() or (parent / "config.json").is_symlink():
        carry["refused"] = "predecessor_not_source"
        return None
    return _source_node(
        parent,
        "repair_predecessor",
        cutoff,
        meter,
        provenance="authenticated_migration",
    )


def _binding(path: Path) -> tuple[bool, str | None, dict[str, Any] | None]:
    try:
        config = guard_source(path)
    except FileNotFoundError:
        return False, "deployment_missing", None
    except OSError:
        return False, "source_unreadable", None
    except ValueError as exc:
        return False, _reason(exc), None
    if not isinstance(config, dict):
        return False, "source_unreadable", None
    return True, None, config


def _carry(path: Path, config: dict[str, Any] | None, meter: _Meter) -> dict[str, Any] | None:
    if not isinstance(config, dict):
        return None
    expected = config.get("migration_sha")
    if type(expected) is not str or len(expected) != 64:
        return None
    blob = meter.read(path / "migration.json")
    if blob is None:
        return {"authenticated": False, "reason": "migration_unreadable", "parent": None}
    if hashlib.sha256(blob).hexdigest() != expected:
        return {"authenticated": False, "reason": "migration_pin", "parent": None}
    try:
        migration = json.loads(blob)
    except (UnicodeError, json.JSONDecodeError):
        return {"authenticated": False, "reason": "migration_malformed", "parent": None}
    if not isinstance(migration, dict):
        return {"authenticated": False, "reason": "migration_malformed", "parent": None}
    if (
        migration.get("kind") != "source_audit_deadline_repair"
        or migration.get("lineage") != "technical_repair_genesis"
        or migration.get("old_chain_rewritten") is not False
    ):
        return {"authenticated": False, "reason": "migration_unauthenticated", "parent": None}
    old_attempts = migration.get("old_attempts")
    new_attempts = migration.get("new_attempts")
    if type(old_attempts) is not int or old_attempts != new_attempts or old_attempts < 0:
        return {"authenticated": False, "reason": "carry_conflict", "parent": None}
    parent_raw = migration.get("parent_root")
    seed_blob = meter.read(path / "seed.json")
    if type(parent_raw) is not str or seed_blob is None:
        return {"authenticated": False, "reason": "migration_unauthenticated", "parent": None}
    try:
        seed = json.loads(seed_blob)
    except (UnicodeError, json.JSONDecodeError):
        return {"authenticated": False, "reason": "migration_unauthenticated", "parent": None}
    if (
        not isinstance(seed, dict)
        or seed.get("lineage") != "technical_repair_genesis"
        or seed.get("parent") != parent_raw
        or seed.get("inherited_journal_head") != migration.get("old_ledger_head")
    ):
        return {"authenticated": False, "reason": "migration_unauthenticated", "parent": None}
    parent = Path(parent_raw)
    if not parent.is_absolute():
        return {"authenticated": False, "reason": "migration_unauthenticated", "parent": None}
    return {
        "authenticated": True,
        "reason": None,
        "attempts": old_attempts,
        "parent": parent,
        "old_stop_sha": _sha_token(migration.get("old_stop_sha")),
        "refused": None,
    }


def _journal(path: Path, cutoff: int, meter: _Meter) -> dict[str, Any]:
    journal = path / "journal"
    empty = _journal_body(present=False, chain_ok=None, unknown=False)
    if not journal.exists():
        return empty
    if journal.is_symlink() or not journal.is_dir() or _protected(journal):
        return _journal_body(present=True, chain_ok=False, unknown=True, reason="journal_refused")
    binding_blob = meter.read(journal / "binding.json")
    if binding_blob is None:
        return _journal_body(
            present=True,
            chain_ok=False,
            unknown=True,
            reason="truncated" if meter.truncated else "journal_unreadable",
        )
    try:
        binding = json.loads(binding_blob)
    except (UnicodeError, json.JSONDecodeError):
        return _journal_body(present=True, chain_ok=False, unknown=True, reason="journal_malformed")
    if not isinstance(binding, dict):
        return _journal_body(present=True, chain_ok=False, unknown=True, reason="journal_malformed")
    scope = binding.get("scope")
    if scope is not None and scope != "B08_source":
        return _journal_body(present=True, chain_ok=False, unknown=True, reason="journal_binding")
    config_sha = binding.get("config_sha")
    config_blob = meter.read(path / "config.json")
    if type(config_sha) is str and (
        config_blob is None or hashlib.sha256(config_blob).hexdigest() != config_sha
    ):
        reason = "truncated" if config_blob is None and meter.truncated else "journal_binding"
        return _journal_body(present=True, chain_ok=False, unknown=True, reason=reason)
    names = _event_names(journal, meter)
    if names is None:
        return _journal_body(present=True, chain_ok=False, unknown=True, reason="truncated")
    expected = [f"event-{i:06d}.json" for i in range(len(names))]
    if names != expected:
        return _journal_body(present=True, chain_ok=False, unknown=True, reason="journal_sequence")
    previous = digest(binding)
    counts: dict[str, int] = {}
    times: list[int] = []
    state: dict[str, int] | None = None
    verified = 0
    partial = False
    reason = None
    for index, name in enumerate(names):
        blob = meter.read(journal / name)
        if blob is None:
            partial = True
            reason = "truncated"
            break
        try:
            row = json.loads(blob)
        except (UnicodeError, json.JSONDecodeError):
            partial = True
            reason = "journal_malformed"
            break
        if not isinstance(row, dict):
            partial = True
            reason = "journal_malformed"
            break
        checksum = row.pop("sha256", None)
        if (
            type(checksum) is not str
            or row.get("previous") != previous
            or row.get("sequence") != index
            or digest(row) != checksum
        ):
            partial = True
            reason = "journal_chain"
            break
        previous = checksum
        verified += 1
        stamp = row.get("time_ns")
        if type(stamp) is not int or stamp > cutoff:
            continue
        times.append(stamp)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        found = _state_counters(payload.get("state"))
        if found is not None:
            state = found
        if row.get("kind") == "outcome":
            status = _token(payload.get("status")) or "unknown"
            counts[status] = counts.get(status, 0) + 1
    chain_ok = not partial and reason is None
    body = _journal_body(
        present=True,
        chain_ok=chain_ok,
        unknown=partial,
        reason=reason,
    )
    body.update(
        {
            "events_verified": verified,
            "events_at_cutoff": len(times),
            "partial": partial,
            "outcome_counts": counts,
            "transport_outcomes": counts.get("transport", 0),
            "attempts": None if state is None else state["attempts"],
            "consecutive_failures": None if state is None else state["failures"],
            "event_times_ns": times,
        }
    )
    return body


def _journal_body(
    *,
    present: bool,
    chain_ok: bool | None,
    unknown: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "present": present,
        "chain_ok": chain_ok,
        "unknown": unknown,
        "reason": reason,
        "events_verified": 0,
        "events_at_cutoff": 0,
        "partial": False,
        "outcome_counts": {},
        "transport_outcomes": 0,
        "attempts": None,
        "consecutive_failures": None,
        "event_times_ns": [],
    }


def _event_names(journal: Path, meter: _Meter) -> list[str] | None:
    names: list[str] = []
    try:
        children = list(journal.iterdir())
    except OSError:
        return None
    for path in children:
        if meter.full():
            meter.truncated = True
            return None
        meter.note()
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.startswith("event-") and path.suffix == ".json":
            names.append(path.name)
    names.sort()
    return names


def _state_counters(state: Any) -> dict[str, int] | None:
    if not isinstance(state, dict):
        return None
    attempts = state.get("attempts")
    failures = state.get("failures")
    if type(attempts) is not int or type(failures) is not int:
        return None
    if attempts < 0 or failures < 0:
        return None
    return {"attempts": attempts, "failures": failures}


def _gaps(journal: dict[str, Any]) -> dict[str, Any]:
    times = [item for item in journal.get("event_times_ns") or [] if type(item) is int]
    silent: list[dict[str, int]] = []
    clock_regression = False
    for earlier, later in zip(times, times[1:], strict=False):
        if later < earlier:
            clock_regression = True
            continue
        gap = later - earlier
        if gap >= SILENT_GAP_NS:
            silent.append({"after_ns": earlier, "before_ns": later, "silent_ns": gap})
    silent.sort(key=lambda item: item["silent_ns"], reverse=True)
    truncated = len(silent) > MAX_GAPS
    return {
        "evidence": "journal_event_times",
        "continuous_uptime": False,
        "uptime_known": False,
        "first_event_ns": times[0] if times else None,
        "last_event_ns": times[-1] if times else None,
        "silent": silent[:MAX_GAPS],
        "silent_truncated": truncated,
        "clock_regression": clock_regression,
    }


def _stop(path: Path, cutoff: int, meter: _Meter) -> dict[str, Any]:
    marker = path / "STOP"
    body: dict[str, Any] = {
        "present": False,
        "sha256": None,
        "reason": None,
        "at_ns": None,
        "applies_at_cutoff": None,
        "symlink": False,
    }
    if not marker.exists():
        return body
    if marker.is_symlink() or not marker.is_file():
        body["symlink"] = marker.is_symlink()
        return body
    blob = meter.read(marker)
    if blob is None:
        body["present"] = True
        body["applies_at_cutoff"] = None
        return body
    body["present"] = True
    body["sha256"] = hashlib.sha256(blob).hexdigest()
    try:
        data = json.loads(blob)
    except (UnicodeError, json.JSONDecodeError):
        return body
    if not isinstance(data, dict):
        return body
    body["reason"] = _token(data.get("reason"))
    at_ns = data.get("at_ns")
    if type(at_ns) is int and at_ns >= 0:
        body["at_ns"] = at_ns
        body["applies_at_cutoff"] = at_ns <= cutoff
    return body


def _incidents(path: Path, meter: _Meter) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    truncated = False
    try:
        found = sorted(path.glob("incident-*.json"))
    except OSError:
        return {"items": [], "truncated": True, "unknown": True}
    for incident in found:
        if len(rows) >= MAX_INCIDENTS or meter.full():
            truncated = True
            break
        match = INCIDENT_RE.fullmatch(incident.name)
        if match is None or incident.is_symlink() or not incident.is_file():
            continue
        blob = meter.read(incident)
        if blob is None:
            truncated = True
            break
        item: dict[str, Any] = {
            "id": match.group(1),
            "at_ns": int(match.group(1)),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "category": None,
            "exit_code": None,
        }
        try:
            data = json.loads(blob)
        except (UnicodeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            item["category"] = _token(data.get("category"))
            exit_code = data.get("exit_code")
            if type(exit_code) is int:
                item["exit_code"] = exit_code
        rows.append(item)
    return {"items": rows, "truncated": truncated, "unknown": truncated}


def _health(path: Path, cutoff: int, meter: _Meter) -> dict[str, Any] | None:
    blob = meter.read(path / "health.json")
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except (UnicodeError, json.JSONDecodeError):
        return {"unreadable": True}
    if not isinstance(data, dict):
        return {"unreadable": True}
    at_ns = data.get("at_ns")
    if type(at_ns) is not int or at_ns > cutoff:
        return None
    kept: dict[str, Any] = {"at_ns": at_ns}
    for key in ("attempts", "failures", "cooldown_ns"):
        value = data.get(key)
        if type(value) is int and value >= 0:
            kept[key] = value
    return kept


def _seed_attempts(path: Path, meter: _Meter) -> int | None:
    blob = meter.read(path / "provider-seed.json")
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    attempts = data.get("attempts")
    if type(attempts) is not int or attempts < 0:
        return None
    return attempts


def _json_file(path: Path, meter: _Meter) -> dict[str, Any] | None:
    blob = meter.read(path)
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except (UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _budget(
    current: dict[str, Any],
    original: dict[str, Any] | None,
    repair: dict[str, Any] | None,
) -> dict[str, Any]:
    config = current.get("config") if isinstance(current.get("config"), dict) else {}
    journal = current["journal"]
    prior = config.get("prior_provider_attempts")
    maximum = config.get("max_requests")
    carry = _single_carry(current, original)
    original_attempts = None
    if isinstance(original, dict) and isinstance(original.get("node"), dict):
        original_attempts = original["node"]["journal"].get("attempts")
    repair_attempts = None if repair is None else repair["journal"].get("attempts")
    return {
        "current_journal_attempts": journal.get("attempts"),
        "current_seed_attempts": current.get("seed_attempts"),
        "current_consecutive_failures": journal.get("consecutive_failures"),
        "current_transport_outcomes": journal.get("transport_outcomes"),
        "prior_provider_attempts": prior if type(prior) is int and prior >= 0 else None,
        "carried_attempts": carry,
        "original_journal_attempts": original_attempts,
        "repair_journal_attempts": repair_attempts,
        "max_requests": maximum if type(maximum) is int and maximum >= 0 else None,
        "summed": False,
    }


def _single_carry(current: dict[str, Any], original: dict[str, Any] | None) -> int | None:
    found: list[int] = []
    for node in (current, (original or {}).get("node") if isinstance(original, dict) else None):
        if not isinstance(node, dict):
            continue
        carry = node.get("carry")
        if isinstance(carry, dict) and carry.get("authenticated") is True:
            attempts = carry.get("attempts")
            if type(attempts) is int:
                found.append(attempts)
    if len(found) != 1:
        return None
    return found[0]


def _empty_budget() -> dict[str, Any]:
    return {
        "current_journal_attempts": None,
        "current_seed_attempts": None,
        "current_consecutive_failures": None,
        "current_transport_outcomes": None,
        "prior_provider_attempts": None,
        "carried_attempts": None,
        "original_journal_attempts": None,
        "repair_journal_attempts": None,
        "max_requests": None,
        "summed": False,
    }


def _active_stop(stop: dict[str, Any]) -> bool | None:
    if stop.get("symlink"):
        return None
    if not stop["present"]:
        return False
    applies = stop.get("applies_at_cutoff")
    if type(applies) is bool:
        return applies
    return None


def _successor_status(root: Path, current: Path, node: dict[str, Any]) -> dict[str, Any]:
    active = _same(current, root / "source") and (root / "successor-ready.json").is_file()
    stop = node["stop"]
    return {
        "active": active,
        "phase": "successor" if active else "borrowed",
        "stopped": _active_stop(stop),
        "stop_sha256": stop["sha256"],
        "stop_reason": stop["reason"],
    }


def _original_stop(
    current: Path,
    original: dict[str, Any] | None,
    current_node: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(original, dict):
        return {"same_as_current": False, "distinct": False, "stopped": None, "stop_sha256": None}
    if original.get("same_as_current") is True:
        stop = current_node["stop"]
        return {
            "same_as_current": True,
            "distinct": False,
            "stopped": _active_stop(stop),
            "stop_sha256": stop["sha256"],
            "stop_reason": stop["reason"],
        }
    node = original.get("node")
    if not isinstance(node, dict):
        return {
            "same_as_current": False,
            "distinct": False,
            "stopped": None,
            "stop_sha256": None,
            "reason": original.get("reason"),
        }
    stop = node["stop"]
    current_sha = current_node["stop"]["sha256"]
    distinct = stop["sha256"] != current_sha or stop["present"] != current_node["stop"]["present"]
    return {
        "same_as_current": False,
        "distinct": distinct and not _same(current, node["path"]),
        "stopped": _active_stop(stop),
        "stop_sha256": stop["sha256"],
        "stop_reason": stop["reason"],
    }


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    journal = dict(node["journal"])
    journal.pop("event_times_ns", None)
    carry = node.get("carry")
    return {
        "role": node["role"],
        "provenance": node["provenance"],
        "binding_ok": node["binding_ok"],
        "binding_reason": node["binding_reason"],
        "stop": node["stop"],
        "incidents": node["incidents"],
        "journal": journal,
        "gaps": node["gaps"],
        "health_consecutive_failures": _health_failures(node.get("health")),
        "seed_attempts": node.get("seed_attempts"),
        "carry_authenticated": bool(isinstance(carry, dict) and carry.get("authenticated") is True),
        "carry_attempts": carry.get("attempts") if isinstance(carry, dict) else None,
    }


def _public_original(original: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(original, dict):
        return None
    if original.get("same_as_current") is True:
        return {"same_as_current": True}
    node = original.get("node")
    if not isinstance(node, dict):
        return {"same_as_current": False, "ok": False, "reason": original.get("reason")}
    public = _public_node(node)
    public["same_as_current"] = False
    return public


def _health_failures(health: dict[str, Any] | None) -> int | None:
    if not isinstance(health, dict) or health.get("unreadable"):
        return None
    failures = health.get("failures")
    return failures if type(failures) is int else None


def _notices(
    current: dict[str, Any],
    original: dict[str, Any] | None,
    repair: dict[str, Any] | None,
) -> list[str]:
    notices: list[str] = []
    for node in (current, (original or {}).get("node") if isinstance(original, dict) else None):
        if not isinstance(node, dict):
            continue
        carry = node.get("carry")
        if isinstance(carry, dict) and carry.get("refused"):
            token = _token(carry.get("refused"))
            if token and token not in notices:
                notices.append(token)
        if isinstance(carry, dict) and carry.get("authenticated") is False:
            token = _token(carry.get("reason"))
            if token and token not in notices:
                notices.append(token)
    if repair is not None and repair["journal"].get("chain_ok") is False:
        notices.append("repair_journal_unverified")
    if isinstance(original, dict) and original.get("ok") is False and original.get("reason"):
        token = _token(str(original["reason"]))
        if token and token not in notices:
            notices.append(token)
    return notices


def _ok(current: dict[str, Any], meter: _Meter) -> bool:
    journal = current["journal"]
    if meter.truncated or not current["binding_ok"]:
        return False
    if journal["present"] and journal["chain_ok"] is not True:
        return False
    if current["incidents"]["unknown"]:
        return False
    return True


def _top_reason(current: dict[str, Any], meter: _Meter) -> str:
    if not current["binding_ok"]:
        return current["binding_reason"] or "source_binding"
    if meter.truncated:
        return "truncated"
    journal = current["journal"]
    if journal["present"] and journal["chain_ok"] is not True:
        return journal["reason"] or "journal_chain"
    if current["incidents"]["unknown"]:
        return "incidents_truncated"
    return "source_lineage_incomplete"


def _reason(exc: BaseException) -> str:
    text = str(exc)
    return text if _token(text) else "source_unreadable"


def _token(value: Any) -> str | None:
    if type(value) is not str or TOKEN_RE.fullmatch(value) is None:
        return None
    return value


def _sha_token(value: Any) -> str | None:
    if type(value) is not str or len(value) != 64:
        return None
    if any(ch not in "0123456789abcdef" for ch in value):
        return None
    return value


def _same(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _protected(path: Path) -> bool:
    for part in path.parts:
        low = part.lower()
        if "holdout" in low or low == "f01" or low.startswith(("f01-", "f01_")):
            return True
        if low == "d43" or low.startswith(("d43-", "d43_")):
            return True
    return False


def _symlink_component(path: Path) -> bool:
    if path.is_symlink():
        return True
    cursor = Path(path.anchor)
    parts = path.parts[1:] if path.is_absolute() else path.parts
    if path.is_absolute():
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return True
        return False
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return True
    return False


class _Meter:
    def __init__(self, max_files: int, max_bytes: int) -> None:
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0
        self.truncated = False

    def full(self) -> bool:
        return self.files >= self.max_files or self.truncated

    def note(self) -> None:
        if self.files >= self.max_files:
            self.truncated = True
            return
        self.files += 1

    def read(self, path: Path) -> bytes | None:
        if self.full():
            self.truncated = True
            return None
        if path.is_symlink() or not path.is_file() or _protected(path):
            if path.exists() and (path.is_symlink() or _protected(path)):
                self.note()
            return None
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size > self.max_bytes - self.bytes or self.files >= self.max_files:
            self.truncated = True
            return None
        try:
            blob = path.read_bytes()
        except OSError:
            return None
        self.files += 1
        self.bytes += len(blob)
        return blob

    def public(self) -> dict[str, Any]:
        return {
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
            "files_seen": self.files,
            "bytes_seen": self.bytes,
            "truncated": self.truncated,
            "unknown": self.truncated,
        }
