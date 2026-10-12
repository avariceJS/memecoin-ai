"""Fail-closed provider-usage aggregation for the versioned Gecko capture roots."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_rotating_scout_v1 as scout

SNAPSHOT_SCHEMA = "brain-senses-gecko-verified-root-usage-v1"
NS_PER_DAY = 86_400 * scout.NS
ROLLING_NS = 30 * NS_PER_DAY
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class UsageError(ValueError):
    """Incomplete, inconsistent, or malformed same-provider usage evidence."""


def canonical_json(value: Any) -> bytes:
    return scout.canonical_json(value)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise UsageError(reason)


def make_verified_root_snapshot(
    *,
    root: str,
    status: str,
    source_prefix_sha256: str,
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Canonicalize verifier-produced attempt rows into a digest-bound root snapshot.

    `verified_absent` is valid only when the caller's descriptor-bound parent inventory proves
    this root was absent. This helper cannot establish that filesystem fact by itself.
    """
    _require(type(root) is str and bool(root) and not root.startswith("/"), "root_invalid")
    _require(
        status in {"verified_prefix", "verified_none", "verified_absent"}, "root_status_invalid"
    )
    _require(
        type(source_prefix_sha256) is str and HASH_RE.fullmatch(source_prefix_sha256) is not None,
        "source_hash_invalid",
    )
    rows = [dict(row) for row in attempts]
    rows.sort(key=lambda row: (row.get("decision_ns", -1), row.get("request_id", "")))
    core = {
        "schema": SNAPSHOT_SCHEMA,
        "schema_version": 1,
        "root": root,
        "status": status,
        "source_prefix_sha256": source_prefix_sha256,
        "attempts": rows,
        "attempts_sha256": sha256(canonical_json(rows)),
    }
    return {**core, "snapshot_sha256": sha256(canonical_json(core))}


def _validate_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    _require(type(snapshot) is dict, "snapshot_shape_invalid")
    expected = {
        "schema",
        "schema_version",
        "root",
        "status",
        "source_prefix_sha256",
        "attempts",
        "attempts_sha256",
        "snapshot_sha256",
    }
    _require(set(snapshot) == expected, "snapshot_shape_invalid")
    _require(
        snapshot.get("schema") == SNAPSHOT_SCHEMA and snapshot.get("schema_version") == 1,
        "snapshot_schema_invalid",
    )
    _require(
        snapshot.get("status") in {"verified_prefix", "verified_none", "verified_absent"},
        "root_status_invalid",
    )
    _require(type(snapshot.get("root")) is str and bool(snapshot["root"]), "root_invalid")
    _require(
        type(snapshot.get("source_prefix_sha256")) is str
        and HASH_RE.fullmatch(snapshot["source_prefix_sha256"]) is not None,
        "source_hash_invalid",
    )
    attempts_value = snapshot.get("attempts")
    _require(type(attempts_value) is list, "attempts_shape_invalid")
    attempts = cast(list[Any], attempts_value)
    _require(
        snapshot.get("attempts_sha256") == sha256(canonical_json(attempts)), "attempts_hash_invalid"
    )
    core = dict(snapshot)
    claimed = core.pop("snapshot_sha256")
    _require(claimed == sha256(canonical_json(core)), "snapshot_hash_invalid")
    if snapshot["status"] in {"verified_none", "verified_absent"}:
        _require(not attempts, "empty_root_has_attempts")
    if snapshot["status"] == "verified_prefix":
        _require(bool(attempts), "nonempty_root_has_no_attempts")
    normalized = []
    for row in attempts:
        _require(
            type(row) is dict
            and set(row)
            == {
                "request_id",
                "decision_ns",
                "maximum_response_bytes",
                "response_bytes",
                "response_sha256",
            },
            "attempt_shape_invalid",
        )
        _require(type(row["request_id"]) is str and bool(row["request_id"]), "request_id_invalid")
        _require(
            type(row["decision_ns"]) is int and row["decision_ns"] >= 0, "decision_time_invalid"
        )
        _require(
            type(row["maximum_response_bytes"]) is int and row["maximum_response_bytes"] > 0,
            "response_reserve_invalid",
        )
        response_bytes = row["response_bytes"]
        response_hash = row["response_sha256"]
        if response_hash is None:
            _require(response_bytes is None, "ambiguous_response_shape")
        else:
            _require(
                type(response_hash) is str and HASH_RE.fullmatch(response_hash) is not None,
                "response_hash_invalid",
            )
            _require(
                type(response_bytes) is int
                and 0 <= response_bytes <= row["maximum_response_bytes"],
                "response_bytes_invalid",
            )
        normalized.append(dict(row))
    _require(
        normalized == sorted(normalized, key=lambda row: (row["decision_ns"], row["request_id"])),
        "attempt_order_invalid",
    )
    _require(
        len({row["request_id"] for row in normalized}) == len(normalized),
        "duplicate_request_id",
    )
    return normalized


def assess_usage(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    expected_roots: Sequence[str],
    checked_ns: int,
    prospective_request_id: str,
    prospective_maximum_response_bytes: int = scout.MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Aggregate verified same-provider usage, charging unresolved attempts at their max size."""
    _require(type(checked_ns) is int and checked_ns > 0, "checked_time_invalid")
    _require(
        type(prospective_request_id) is str and bool(prospective_request_id),
        "prospective_id_invalid",
    )
    _require(
        type(prospective_maximum_response_bytes) is int and prospective_maximum_response_bytes > 0,
        "prospective_response_reserve_invalid",
    )
    required = list(expected_roots)
    _require(required == sorted(set(required)) and bool(required), "expected_roots_invalid")
    rows = [_validate_snapshot(snapshot) for snapshot in snapshots]
    roots = [str(snapshot["root"]) for snapshot in snapshots]
    _require(roots == required, "root_inventory_incomplete_or_unexpected")
    all_attempts: list[dict[str, Any]] = []
    owner_by_id: dict[str, str] = {}
    for snapshot, attempts in zip(snapshots, rows, strict=True):
        for attempt in attempts:
            request_id = attempt["request_id"]
            _require(request_id not in owner_by_id, "duplicate_request_id_across_roots")
            _require(attempt["decision_ns"] <= checked_ns, "future_decision_time")
            owner_by_id[request_id] = str(snapshot["root"])
            all_attempts.append({**attempt, "root": snapshot["root"]})
    _require(prospective_request_id not in owner_by_id, "prospective_request_id_already_spent")
    all_attempts.append(
        {
            "request_id": prospective_request_id,
            "decision_ns": checked_ns,
            "maximum_response_bytes": prospective_maximum_response_bytes,
            "response_bytes": None,
            "response_sha256": None,
            "root": "prospective",
        }
    )
    day_start = checked_ns - checked_ns % NS_PER_DAY
    rolling_start = checked_ns - ROLLING_NS
    utc_day = [row for row in all_attempts if day_start <= row["decision_ns"] <= checked_ns]
    rolling = [row for row in all_attempts if rolling_start <= row["decision_ns"] <= checked_ns]

    def charged_bytes(rows_in_window: Sequence[Mapping[str, Any]]) -> int:
        return sum(
            row["response_bytes"]
            if row["response_sha256"] is not None
            else row["maximum_response_bytes"]
            for row in rows_in_window
        )

    day_bytes = charged_bytes(utc_day)
    rolling_bytes = charged_bytes(rolling)
    day_requests = len(utc_day)
    rolling_requests = len(rolling)
    reasons = []
    if day_requests > scout.MAX_INTENTS_PER_UTC_DAY:
        reasons.append("utc_day_request_cap")
    if rolling_requests > scout.MAX_INTENTS_PER_ROLLING_30D:
        reasons.append("rolling_request_cap")
    if day_bytes > scout.MAX_RAW_BYTES_PER_UTC_DAY:
        reasons.append("utc_day_raw_byte_cap")
    if rolling_bytes > scout.MAX_RAW_BYTES_PER_ROLLING_30D:
        reasons.append("rolling_raw_byte_cap")
    inventory_core = {
        "checked_ns": checked_ns,
        "expected_roots": required,
        "snapshot_sha256s": [snapshot["snapshot_sha256"] for snapshot in snapshots],
        "prospective_request_id": prospective_request_id,
        "prospective_maximum_response_bytes": prospective_maximum_response_bytes,
    }
    return {
        "schema": "brain-senses-gecko-shared-usage-assessment-v1",
        "inventory_sha256": sha256(canonical_json(inventory_core)),
        "utc_day": {
            "request_count_including_prospective": day_requests,
            "maximum_request_count": scout.MAX_INTENTS_PER_UTC_DAY,
            "charged_response_bytes_including_prospective": day_bytes,
            "maximum_response_bytes": scout.MAX_RAW_BYTES_PER_UTC_DAY,
        },
        "rolling_30d": {
            "request_count_including_prospective": rolling_requests,
            "maximum_request_count": scout.MAX_INTENTS_PER_ROLLING_30D,
            "charged_response_bytes_including_prospective": rolling_bytes,
            "maximum_response_bytes": scout.MAX_RAW_BYTES_PER_ROLLING_30D,
        },
        "unresolved_attempt_count_in_rolling_30d": sum(
            row["response_sha256"] is None for row in rolling
        ),
        "admitted": not reasons,
        "reasons": reasons,
        "provider_io": False,
        "actual_provider_charge": "unknown_unverified",
    }


__all__ = ["UsageError", "assess_usage", "make_verified_root_snapshot"]
