"""Descriptor-friendly, at-most-once journal contract for Gecko pool discovery.

This module is a runtime adapter around the frozen v1 discovery parser and the
v3 capacity ledger. It intentionally contains no credentials, default storage
paths, process spawning, or activation switch. The owner host supplies an
already verified descriptor writer and a v3 coordinator port.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from tools import brain_senses_gecko_new_pool_discovery_v1 as discovery

SCHEMA = "brain-senses-gecko-new-pool-discovery-runtime-v2"
EPOCH_SCHEMA = f"{SCHEMA}-epoch"
PLAN_EVENT_SCHEMA = f"{SCHEMA}-plan-event"
PAGE_INTENT_SCHEMA = f"{SCHEMA}-page-intent"
PAGE_DISPATCH_SCHEMA = f"{SCHEMA}-page-dispatch"
PAGE_RESULT_SCHEMA = f"{SCHEMA}-page-result"
PAGE_CONFIRM_SCHEMA = f"{SCHEMA}-page-confirmation"
RELEASE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
NS = 1_000_000_000
EPOCH_NS = 72 * 60 * 60 * NS
EPOCH_REQUEST_RUNWAY_BYTES = 6_794_772_480
LIFETIME_PHYSICAL_CAP_BYTES = 101_921_587_200
DAY_45_NS = 45 * 24 * 60 * 60 * NS
ALERT_THRESHOLDS = (70, 85, 95)


class RuntimeErrorV2(ValueError):
    """Invalid source, journal, admission, or chronology in the v2 runtime."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeErrorV2(reason)


def _is_hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _is_id(value: Any) -> bool:
    return type(value) is str and RELEASE_ID_RE.fullmatch(value) is not None


def epoch_envelope(
    *,
    release_id: str,
    lease_id: str,
    root_id: str,
    release_sha256: str,
    source_sha256: str,
    policy: Mapping[str, Any],
    epoch_index: int,
    epoch_start_ns: int,
    previous_epoch_sha256: str | None,
    lifetime_requests_before: int,
    lifetime_physical_before: int,
    prior_allocated_physical: int,
    prior_pending_physical: int,
    epoch_runway_committed: int = 0,
    predecessor_standby_sha256: str | None = None,
) -> dict[str, Any]:
    """Compile an immutable 72-hour epoch within the non-resetting umbrella.

    The envelope is indefinite. The historical 45-day point is a diagnostic
    only; it cannot renew or reset identity, counters, cursor, or byte debt.
    """
    checked = discovery.verify_policy(policy)
    _need(
        _is_id(release_id)
        and _is_id(lease_id)
        and _is_id(root_id)
        and _is_hash(release_sha256)
        and _is_hash(source_sha256)
        and type(epoch_index) is int
        and epoch_index >= 0
        and type(epoch_start_ns) is int
        and epoch_start_ns > 0
        and epoch_start_ns % (300 * NS) == 0
        and (previous_epoch_sha256 is None or _is_hash(previous_epoch_sha256))
        and type(lifetime_requests_before) is int
        and lifetime_requests_before >= 0
        and type(lifetime_physical_before) is int
        and 0 <= lifetime_physical_before <= LIFETIME_PHYSICAL_CAP_BYTES
        and type(prior_allocated_physical) is int
        and prior_allocated_physical >= 0
        and prior_allocated_physical <= lifetime_physical_before
        and type(prior_pending_physical) is int
        and prior_pending_physical >= 0,
        "runtime_epoch_inputs",
    )
    _need(
        type(epoch_runway_committed) is int
        and 0 <= epoch_runway_committed <= EPOCH_REQUEST_RUNWAY_BYTES,
        "runtime_epoch_runway_commitment",
    )
    _need(checked["max_scan_count"] == 864, "runtime_epoch_scan_count")
    _need(checked["scan_interval_seconds"] == 300, "runtime_epoch_scan_interval")
    _need(checked["page_range"] == [1, 2, 3], "runtime_epoch_pages")
    _need(
        checked["caps"]["max_physical_reserve_per_lease"] == EPOCH_REQUEST_RUNWAY_BYTES,
        "runtime_epoch_runway_policy",
    )
    _need(
        lifetime_physical_before + prior_pending_physical <= LIFETIME_PHYSICAL_CAP_BYTES,
        "runtime_lifetime_physical_cap",
    )
    # Prior settled allocation is consumed under the non-resetting lifetime
    # cap. It does not fund this epoch's runway. Only writes already explicitly
    # committed against this same epoch reduce its uncommitted top-up.
    epoch_remaining = EPOCH_REQUEST_RUNWAY_BYTES - epoch_runway_committed
    core = {
        "schema": EPOCH_SCHEMA,
        "schema_version": 2,
        "release_id": release_id,
        "lease_id": lease_id,
        "root_id": root_id,
        "release_sha256": release_sha256,
        "source_sha256": source_sha256,
        "policy_sha256": checked["policy_sha256"],
        "policy_contract_sha256": policy_contract_sha256(checked),
        "epoch_index": epoch_index,
        "epoch_start_ns": epoch_start_ns,
        "epoch_end_ns": epoch_start_ns + EPOCH_NS,
        "previous_epoch_sha256": previous_epoch_sha256,
        "predecessor_standby_sha256": predecessor_standby_sha256,
        "lifetime_requests_before": lifetime_requests_before,
        "lifetime_physical_before": lifetime_physical_before,
        "prior_allocated_physical": prior_allocated_physical,
        "prior_pending_physical": prior_pending_physical,
        "epoch_runway_committed": epoch_runway_committed,
        "next_epoch_runway_bytes": epoch_remaining,
        "lifetime_physical_cap_bytes": LIFETIME_PHYSICAL_CAP_BYTES,
        "day_45_is_diagnostic_only": True,
        "cursor_reset": False,
        "policy_change_requires_review": True,
    }
    result = {**core, "epoch_sha256": sha256(canonical(core))}
    verify_epoch_envelope(result, policy=checked)
    return result


def verify_epoch_envelope(value: Any, *, policy: Mapping[str, Any]) -> dict[str, Any]:
    checked = discovery.verify_policy(policy)
    keys = {
        "schema",
        "schema_version",
        "release_id",
        "lease_id",
        "root_id",
        "release_sha256",
        "source_sha256",
        "policy_sha256",
        "policy_contract_sha256",
        "epoch_index",
        "epoch_start_ns",
        "epoch_end_ns",
        "previous_epoch_sha256",
        "predecessor_standby_sha256",
        "lifetime_requests_before",
        "lifetime_physical_before",
        "prior_allocated_physical",
        "prior_pending_physical",
        "next_epoch_runway_bytes",
        "epoch_runway_committed",
        "lifetime_physical_cap_bytes",
        "day_45_is_diagnostic_only",
        "cursor_reset",
        "policy_change_requires_review",
        "epoch_sha256",
    }
    _need(type(value) is dict and set(value) == keys, "runtime_epoch_shape")
    core = dict(value)
    digest = core.pop("epoch_sha256")
    _need(
        value["schema"] == EPOCH_SCHEMA
        and value["schema_version"] == 2
        and _is_id(value["release_id"])
        and _is_id(value["lease_id"])
        and _is_id(value["root_id"])
        and _is_hash(value["release_sha256"])
        and _is_hash(value["source_sha256"])
        and value["policy_sha256"] == checked["policy_sha256"]
        and value["policy_contract_sha256"] == policy_contract_sha256(checked)
        and type(value["epoch_index"]) is int
        and value["epoch_index"] >= 0
        and type(value["epoch_start_ns"]) is int
        and value["epoch_start_ns"] > 0
        and value["epoch_end_ns"] == value["epoch_start_ns"] + EPOCH_NS
        and (value["previous_epoch_sha256"] is None or _is_hash(value["previous_epoch_sha256"]))
        and (
            value["predecessor_standby_sha256"] is None
            or _is_hash(value["predecessor_standby_sha256"])
        )
        and type(value["lifetime_requests_before"]) is int
        and value["lifetime_requests_before"] >= 0
        and type(value["lifetime_physical_before"]) is int
        and 0 <= value["lifetime_physical_before"] <= LIFETIME_PHYSICAL_CAP_BYTES
        and type(value["prior_allocated_physical"]) is int
        and value["prior_allocated_physical"] >= 0
        and value["prior_allocated_physical"] <= value["lifetime_physical_before"]
        and type(value["prior_pending_physical"]) is int
        and value["prior_pending_physical"] >= 0
        and value["lifetime_physical_before"] + value["prior_pending_physical"]
        <= LIFETIME_PHYSICAL_CAP_BYTES
        and type(value["epoch_runway_committed"]) is int
        and 0 <= value["epoch_runway_committed"] <= EPOCH_REQUEST_RUNWAY_BYTES
        and value["next_epoch_runway_bytes"]
        == EPOCH_REQUEST_RUNWAY_BYTES - value["epoch_runway_committed"]
        and value["lifetime_physical_cap_bytes"] == LIFETIME_PHYSICAL_CAP_BYTES
        and value["day_45_is_diagnostic_only"] is True
        and value["cursor_reset"] is False
        and value["policy_change_requires_review"] is True
        and digest == sha256(canonical(core)),
        "runtime_epoch_binding",
    )
    return dict(value)


def policy_contract_sha256(policy: Mapping[str, Any]) -> str:
    """Hash invariant policy fields while excluding the epoch's first timestamp."""
    checked = discovery.verify_policy(policy)
    core = dict(checked)
    for key in ("policy_sha256", "first_scan_ns", "lease_duration_seconds"):
        core.pop(key, None)
    return sha256(canonical(core))


def make_epoch_seal(
    *,
    epoch: Mapping[str, Any],
    scan_count: int,
    unresolved_count: int,
    terminal_replay_sha256: str,
    coordinator_prefix_sha256: str,
    lifetime_requests_after: int,
    lifetime_physical_after: int,
    allocated_physical_after: int,
    pending_physical_after: int,
    transport_quiescent: bool,
    sealed_at_ns: int,
) -> dict[str, Any]:
    """Create a terminal proof object; the host must derive it from real replay."""
    _need(
        type(scan_count) is int
        and scan_count == 864
        and type(unresolved_count) is int
        and unresolved_count == 0
        and _is_hash(terminal_replay_sha256)
        and _is_hash(coordinator_prefix_sha256)
        and type(lifetime_requests_after) is int
        and lifetime_requests_after >= 0
        and type(lifetime_physical_after) is int
        and 0 <= lifetime_physical_after <= LIFETIME_PHYSICAL_CAP_BYTES
        and type(allocated_physical_after) is int
        and allocated_physical_after >= 0
        and type(pending_physical_after) is int
        and pending_physical_after >= 0
        and lifetime_physical_after + pending_physical_after <= LIFETIME_PHYSICAL_CAP_BYTES
        and transport_quiescent is True
        and type(sealed_at_ns) is int
        and sealed_at_ns >= epoch["epoch_end_ns"],
        "runtime_epoch_seal_inputs",
    )
    core = {
        "schema": f"{SCHEMA}-epoch-seal",
        "schema_version": 2,
        "epoch_sha256": epoch["epoch_sha256"],
        "scan_count": scan_count,
        "unresolved_count": unresolved_count,
        "terminal_replay_sha256": terminal_replay_sha256,
        "coordinator_prefix_sha256": coordinator_prefix_sha256,
        "lifetime_requests_after": lifetime_requests_after,
        "lifetime_physical_after": lifetime_physical_after,
        "allocated_physical_after": allocated_physical_after,
        "pending_physical_after": pending_physical_after,
        "transport_quiescent": True,
        "sealed_at_ns": sealed_at_ns,
    }
    return {**core, "seal_sha256": sha256(canonical(core))}


def successor_epoch(
    *,
    previous_epoch: Mapping[str, Any],
    seal: Mapping[str, Any],
    policy: Mapping[str, Any],
    lease_id: str,
    root_id: str,
    predecessor_standby_sha256: str | None = None,
) -> dict[str, Any]:
    """Prepare the next continuous epoch only from an exact terminal predecessor."""
    previous = verify_epoch_envelope(previous_epoch, policy=policy)
    seal_core = dict(seal)
    seal_hash = seal_core.pop("seal_sha256", None)
    expected = {
        "schema",
        "schema_version",
        "epoch_sha256",
        "scan_count",
        "unresolved_count",
        "terminal_replay_sha256",
        "coordinator_prefix_sha256",
        "lifetime_requests_after",
        "lifetime_physical_after",
        "allocated_physical_after",
        "pending_physical_after",
        "transport_quiescent",
        "sealed_at_ns",
    }
    _need(set(seal_core) == expected, "runtime_successor_seal_shape")
    _need(
        seal_core["schema"] == f"{SCHEMA}-epoch-seal"
        and seal_core["schema_version"] == 2
        and seal_core["epoch_sha256"] == previous["epoch_sha256"]
        and seal_core["scan_count"] == 864
        and seal_core["unresolved_count"] == 0
        and _is_hash(seal_core["terminal_replay_sha256"])
        and _is_hash(seal_core["coordinator_prefix_sha256"])
        and seal_core["transport_quiescent"] is True
        and type(seal_core["sealed_at_ns"]) is int
        and seal_core["sealed_at_ns"] >= previous["epoch_end_ns"]
        and seal_hash == sha256(canonical(seal_core)),
        "runtime_successor_seal_unverified",
    )
    checked = discovery.verify_policy(policy)
    _need(
        policy_contract_sha256(checked) == previous["policy_contract_sha256"],
        "runtime_successor_policy_changed",
    )
    # Each epoch has an immutable policy hash because first_scan_ns is pinned.
    # The invariant contract must match, while the epoch policy is recompiled
    # for the next anchor and stored alongside that epoch's evidence.
    next_policy = discovery.compile_policy(
        first_scan_ns=previous["epoch_end_ns"],
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=checked["sample"]["numerator"],
        sample_denominator=checked["sample"]["denominator"],
        source_spec_sha256=checked["source_spec_sha256"],
        max_scan_count=864,
        max_raw_bytes_per_page=checked["caps"]["max_raw_bytes_per_page"],
    )
    return epoch_envelope(
        release_id=previous["release_id"],
        lease_id=lease_id,
        root_id=root_id,
        release_sha256=previous["release_sha256"],
        source_sha256=previous["source_sha256"],
        policy=next_policy,
        epoch_index=previous["epoch_index"] + 1,
        epoch_start_ns=previous["epoch_end_ns"],
        previous_epoch_sha256=seal["seal_sha256"],
        lifetime_requests_before=seal_core["lifetime_requests_after"],
        lifetime_physical_before=seal_core["lifetime_physical_after"],
        prior_allocated_physical=seal_core["allocated_physical_after"],
        prior_pending_physical=seal_core["pending_physical_after"],
        epoch_runway_committed=0,
        predecessor_standby_sha256=predecessor_standby_sha256,
    )


def verify_epoch_history(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Replay immutable epoch policy/evidence without substituting current policy.

    Each entry carries the exact policy bytes' parsed value, epoch envelope, and
    terminal seal. Only the last entry may be unsealed. Epoch-specific policy
    hashes are expected to differ because each freezes its own first scan time;
    the invariant policy contract and source/release pins must remain stable.
    """
    _need(type(entries) in (list, tuple) and len(entries) > 0, "runtime_epoch_history_empty")
    prior_epoch: dict[str, Any] | None = None
    prior_seal: dict[str, Any] | None = None
    prior_policy_contract: str | None = None
    release_id: str | None = None
    source_sha: str | None = None
    seen_roots: set[str] = set()
    seen_leases: set[str] = set()
    for index, entry in enumerate(entries):
        _need(
            type(entry) is dict and set(entry) == {"policy", "epoch", "seal"},
            "runtime_epoch_history_entry",
        )
        policy = discovery.verify_policy(entry["policy"])
        epoch = verify_epoch_envelope(entry["epoch"], policy=policy)
        final = index == len(entries) - 1
        if final and entry["seal"] is None:
            seal = None
        else:
            _need(entry["seal"] is not None, "runtime_epoch_history_unsealed_predecessor")
            seal = _verify_epoch_seal(entry["seal"], epoch=epoch)
        _need(
            policy["first_scan_ns"] == epoch["epoch_start_ns"],
            "runtime_epoch_policy_anchor_mismatch",
        )
        contract = policy_contract_sha256(policy)
        if index == 0:
            _need(
                epoch["epoch_index"] == 0 and epoch["previous_epoch_sha256"] is None,
                "runtime_epoch_history_genesis",
            )
            release_id = epoch["release_id"]
            source_sha = epoch["source_sha256"]
            prior_policy_contract = contract
        else:
            assert prior_epoch is not None and prior_seal is not None
            _need(
                epoch["epoch_index"] == prior_epoch["epoch_index"] + 1
                and epoch["epoch_start_ns"] == prior_epoch["epoch_end_ns"]
                and epoch["previous_epoch_sha256"] == prior_seal["seal_sha256"]
                and epoch["release_id"] == release_id
                and epoch["source_sha256"] == source_sha
                and contract == prior_policy_contract
                and epoch["root_id"] != prior_epoch["root_id"]
                and epoch["lease_id"] != prior_epoch["lease_id"]
                and epoch["lifetime_requests_before"] == prior_seal["lifetime_requests_after"]
                and epoch["lifetime_physical_before"] == prior_seal["lifetime_physical_after"]
                and epoch["prior_allocated_physical"] == prior_seal["allocated_physical_after"]
                and epoch["prior_pending_physical"] == prior_seal["pending_physical_after"],
                "runtime_epoch_history_link",
            )
        _need(
            epoch["root_id"] not in seen_roots and epoch["lease_id"] not in seen_leases,
            "runtime_epoch_identity_reused",
        )
        seen_roots.add(epoch["root_id"])
        seen_leases.add(epoch["lease_id"])
        prior_epoch = epoch
        prior_seal = seal
    return {
        "schema": f"{SCHEMA}-epoch-history-replay",
        "epoch_count": len(entries),
        "current_epoch_index": prior_epoch["epoch_index"] if prior_epoch else None,
        "current_policy_sha256": entries[-1]["policy"]["policy_sha256"],
        "policy_contract_sha256": prior_policy_contract,
        "sealed": prior_seal is not None,
        "release_id": release_id,
        "source_sha256": source_sha,
    }


def _verify_epoch_seal(value: Any, *, epoch: Mapping[str, Any]) -> dict[str, Any]:
    _need(type(value) is dict, "runtime_epoch_seal_shape")
    core = dict(value)
    digest = core.pop("seal_sha256", None)
    required = {
        "schema",
        "schema_version",
        "epoch_sha256",
        "scan_count",
        "unresolved_count",
        "terminal_replay_sha256",
        "coordinator_prefix_sha256",
        "lifetime_requests_after",
        "lifetime_physical_after",
        "allocated_physical_after",
        "pending_physical_after",
        "transport_quiescent",
        "sealed_at_ns",
    }
    _need(set(core) == required, "runtime_epoch_seal_shape")
    _need(
        core["schema"] == f"{SCHEMA}-epoch-seal"
        and core["schema_version"] == 2
        and core["epoch_sha256"] == epoch["epoch_sha256"]
        and core["scan_count"] == 864
        and core["unresolved_count"] == 0
        and _is_hash(core["terminal_replay_sha256"])
        and _is_hash(core["coordinator_prefix_sha256"])
        and type(core["lifetime_requests_after"]) is int
        and core["lifetime_requests_after"] >= epoch["lifetime_requests_before"]
        and type(core["lifetime_physical_after"]) is int
        and epoch["lifetime_physical_before"] <= core["lifetime_physical_after"]
        and core["lifetime_physical_after"] <= LIFETIME_PHYSICAL_CAP_BYTES
        and type(core["allocated_physical_after"]) is int
        and core["allocated_physical_after"] >= epoch["prior_allocated_physical"]
        and type(core["pending_physical_after"]) is int
        and core["pending_physical_after"] >= 0
        and core["lifetime_physical_after"] + core["pending_physical_after"]
        <= LIFETIME_PHYSICAL_CAP_BYTES
        and core["transport_quiescent"] is True
        and type(core["sealed_at_ns"]) is int
        and core["sealed_at_ns"] >= epoch["epoch_end_ns"]
        and digest == sha256(canonical(core)),
        "runtime_epoch_seal_binding",
    )
    return {**core, "seal_sha256": digest}


def lifetime_alerts(*, start_ns: int, now_ns: int, physical_bytes: int) -> list[str]:
    _need(
        type(start_ns) is int and start_ns > 0 and type(now_ns) is int and now_ns >= start_ns,
        "runtime_alert_time",
    )
    _need(
        type(physical_bytes) is int and 0 <= physical_bytes <= LIFETIME_PHYSICAL_CAP_BYTES,
        "runtime_alert_bytes",
    )
    result: list[str] = []
    if now_ns - start_ns >= DAY_45_NS:
        result.append("day45_diagnostic")
    percent = physical_bytes * 100 // LIFETIME_PHYSICAL_CAP_BYTES
    result.extend(
        f"physical_{threshold}_percent" for threshold in ALERT_THRESHOLDS if percent >= threshold
    )
    return result


def make_page_record(
    *,
    request_id: str,
    status: str,
    request_started_ns: int | None,
    receipt_ns: int | None,
    durable_available_ns: int | None,
    raw_body: bytes | None,
    http_status: int | None,
    body_truncated: bool,
) -> dict[str, Any]:
    raw = raw_body
    return {
        "request_id": request_id,
        "status": status,
        "request_started_ns": request_started_ns,
        "receipt_ns": receipt_ns,
        "durable_available_ns": durable_available_ns,
        "raw_body": raw,
        "raw_sha256": sha256(raw) if raw is not None else None,
        "raw_bytes": len(raw) if raw is not None else 0,
        "http_status": http_status,
        "body_truncated": body_truncated,
    }


class CapacityPort(Protocol):
    def reserve(
        self,
        *,
        request_id: str,
        reservation_id: str,
        intent_path: str,
        outcome_path: str,
        intent_bytes: bytes,
        at_ns: int,
    ) -> None: ...
    def settle(
        self,
        *,
        reservation_id: str,
        outcome: str,
        outcome_bytes: bytes,
        allocated_bytes: int,
        at_ns: int,
        ambiguous: bool = False,
        release: bool = False,
    ) -> None: ...


def compile_epoch_scan_plan(
    *,
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    scan_index: int,
    scheduled_ns: int,
) -> dict[str, Any]:
    """Bind the frozen v1 plan to its immutable epoch namespace."""
    checked = discovery.verify_policy(policy)
    verified_epoch = verify_epoch_envelope(epoch, policy=checked)
    _need(
        checked["first_scan_ns"] == verified_epoch["epoch_start_ns"]
        and 0 <= scan_index < checked["max_scan_count"],
        "runtime_scan_epoch_binding",
    )
    plan = discovery.compile_scan_plan(checked, scan_index=scan_index, scheduled_ns=scheduled_ns)
    binding = {
        "epoch_index": verified_epoch["epoch_index"],
        "epoch_sha256": verified_epoch["epoch_sha256"],
        "discovery_plan_sha256": plan["plan_sha256"],
    }
    return {
        **plan,
        "epoch_index": verified_epoch["epoch_index"],
        "epoch_sha256": verified_epoch["epoch_sha256"],
        "runtime_plan_sha256": sha256(canonical(binding)),
    }


def verify_epoch_scan_plan(
    plan: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the namespace hash and return the exact frozen v1 plan."""
    checked = discovery.verify_policy(policy)
    verified_epoch = verify_epoch_envelope(epoch, policy=checked)
    required = {
        "schema",
        "schema_version",
        "policy_sha256",
        "scan_id",
        "scan_index",
        "scheduled_ns",
        "expires_ns",
        "page_slots",
        "provider_io",
        "plan_sha256",
        "epoch_index",
        "epoch_sha256",
        "runtime_plan_sha256",
    }
    _need(type(plan) is dict and set(plan) == required, "runtime_epoch_plan_shape")
    epoch_fields = {"epoch_index", "epoch_sha256", "runtime_plan_sha256"}
    raw_plan = {key: plan[key] for key in required - epoch_fields}
    binding = {
        "epoch_index": verified_epoch["epoch_index"],
        "epoch_sha256": verified_epoch["epoch_sha256"],
        "discovery_plan_sha256": raw_plan["plan_sha256"],
    }
    expected_plan = discovery.compile_scan_plan(
        checked,
        scan_index=raw_plan["scan_index"],
        scheduled_ns=raw_plan["scheduled_ns"],
    )
    _need(
        raw_plan == expected_plan
        and plan["epoch_index"] == verified_epoch["epoch_index"]
        and plan["epoch_sha256"] == verified_epoch["epoch_sha256"]
        and plan["runtime_plan_sha256"] == sha256(canonical(binding)),
        "runtime_epoch_plan_binding",
    )
    return expected_plan


def verify_persisted_scan(
    *,
    policy: Mapping[str, Any],
    plan: Mapping[str, Any],
    page_records: Sequence[Mapping[str, Any]],
    raw_by_request_id: Mapping[str, bytes],
    previously_seen_pool_ids: Sequence[str],
) -> dict[str, Any]:
    """Independent full denominator replay from durable page records and raw bytes."""
    raw_plan = dict(plan)
    for key in ("epoch_index", "epoch_sha256", "runtime_plan_sha256"):
        raw_plan.pop(key, None)
    if len(raw_plan) != len(plan):
        binding = {
            "epoch_index": plan.get("epoch_index"),
            "epoch_sha256": plan.get("epoch_sha256"),
            "discovery_plan_sha256": raw_plan.get("plan_sha256"),
        }
        _need(
            type(plan.get("epoch_index")) is int
            and _is_hash(plan.get("epoch_sha256"))
            and plan.get("runtime_plan_sha256") == sha256(canonical(binding)),
            "runtime_replay_plan_epoch_binding",
        )
    hydrated: list[dict[str, Any]] = []
    for row in page_records:
        record = dict(row)
        raw = raw_by_request_id.get(record.get("request_id"))
        if record.get("raw_sha256") is None:
            _need(raw is None, "runtime_unexpected_raw_artifact")
        else:
            _need(
                type(raw) is bytes and sha256(raw) == record["raw_sha256"],
                "runtime_raw_artifact_mismatch",
            )
        record["raw_body"] = raw
        hydrated.append(record)
    return discovery.replay_scan(
        policy=policy,
        plan=raw_plan,
        page_slots=hydrated,
        previously_seen_pool_ids=previously_seen_pool_ids,
    )
