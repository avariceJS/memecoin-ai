"""Independent replay for the separately versioned Gecko scout request ledger v2."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, cast

from brain_senses_external_clock_v1 import ClockError, validate_progress
from brain_senses_gecko_lane_engine_v3 import parse_response

from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools import brain_senses_gecko_scout_chain_v1 as chain
from tools.brain_senses_external_writer_v1 import canonical_json

INTENT_SCHEMA = "brain-senses-gecko-capture-only-v2-intent-v1"
DISPATCH_SCHEMA = "brain-senses-gecko-capture-only-v2-dispatch-v1"
TERMINAL_SCHEMA = "brain-senses-gecko-capture-only-v2-terminal-v1"
CONFIRMATION_SCHEMA = "brain-senses-gecko-capture-only-v2-confirmation-v1"
ROOT_PREFIX = chain.ROOT_PREFIX
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MINIMUM_DISPATCH_SPACING_NS = 12 * scout.NS
ROLLING_DISPATCH_WINDOW_NS = 60 * scout.NS
WINDOW_START_NS = 60 * scout.NS
REQUEST_START_END_NS = 270 * scout.NS
AVAILABILITY_END_NS = 300 * scout.NS
MAX_RESPONSE_BYTES = scout.MAX_RESPONSE_BYTES
_RETRY_AFTER_TOKEN = re.compile(r"[0-9]+\Z")
_RETRY_AFTER_CAPTURE_MAX = 64


class VerifyError(ValueError):
    """Malformed or inconsistent v2 request journal."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise VerifyError(reason)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _retry_after_class(row: Mapping[str, Any]) -> tuple[int | None, str]:
    count = row.get("retry_after_header_count")
    evidence = row.get("retry_after_evidence")
    _require(type(count) is int and count >= 0, "v2_retry_after_count")
    _require(type(evidence) is list and len(evidence) == min(count, 2), "v2_retry_after_evidence")
    for item in evidence:
        _require(
            type(item) is dict
            and set(item) == {"captured", "original_length"}
            and type(item.get("captured")) is str
            and type(item.get("original_length")) is int
            and 0 <= len(item["captured"]) <= _RETRY_AFTER_CAPTURE_MAX
            and item["original_length"] >= len(item["captured"]),
            "v2_retry_after_evidence",
        )
    if count > 1:
        return None, "duplicate"
    if count == 0:
        return None, "absent"
    _require(len(evidence) == 1, "v2_retry_after_evidence")
    item = evidence[0]
    raw = cast(str, item["captured"])
    if item["original_length"] > _RETRY_AFTER_CAPTURE_MAX:
        return None, "oversize"
    token = raw.strip(" \t")
    if len(token) > _RETRY_AFTER_CAPTURE_MAX:
        return None, "oversize"
    if _RETRY_AFTER_TOKEN.fullmatch(token) is None:
        return None, "malformed"
    normalized = token.lstrip("0") or "0"
    if len(normalized) > 5:
        return None, "over_24h"
    seconds = int(normalized)
    if seconds > 86_400:
        return None, "over_24h"
    return seconds, "valid"


def _record_hash(row: Mapping[str, Any], field: str, reason: str) -> None:
    core = dict(row)
    claimed = core.pop(field, None)
    _require(type(claimed) is str and claimed == sha256(canonical_json(core)), reason)


def _admission_is_bound(admission: Mapping[str, Any], request_id: str, decision_ns: int) -> bool:
    snapshots = admission.get("root_snapshot_sha256s")
    roots = admission.get("expected_roots")
    day = admission.get("utc_day")
    rolling = admission.get("rolling_30d")
    inventory_core = {
        "checked_ns": admission.get("checked_ns"),
        "expected_roots": roots,
        "root_inventory_sha256": admission.get("root_inventory_sha256"),
        "snapshot_sha256s": snapshots,
        "prospective_request_id": admission.get("prospective_request_id"),
        "prospective_maximum_response_bytes": admission.get("prospective_maximum_response_bytes"),
    }
    return (
        admission.get("schema") == "brain-senses-gecko-shared-usage-assessment-v1"
        and admission.get("admitted") is True
        and admission.get("reasons") == []
        and admission.get("checked_ns") == decision_ns
        and admission.get("prospective_request_id") == request_id
        and admission.get("prospective_maximum_response_bytes") == MAX_RESPONSE_BYTES
        and admission.get("provider_io") is False
        and admission.get("actual_provider_charge") == "unknown_unverified"
        and type(admission.get("root_inventory_sha256")) is str
        and HASH_RE.fullmatch(cast(str, admission.get("root_inventory_sha256"))) is not None
        and type(roots) is list
        and bool(roots)
        and all(type(value) is str and bool(value) for value in roots)
        and roots == sorted(set(roots))
        and type(admission.get("inventory_sha256")) is str
        and admission.get("inventory_sha256") == sha256(canonical_json(inventory_core))
        and type(snapshots) is list
        and bool(snapshots)
        and all(type(value) is str and HASH_RE.fullmatch(value) is not None for value in snapshots)
        and type(day) is dict
        and day.get("maximum_request_count") == scout.MAX_INTENTS_PER_UTC_DAY
        and type(day.get("request_count_including_prospective")) is int
        and 1 <= day["request_count_including_prospective"] <= scout.MAX_INTENTS_PER_UTC_DAY
        and day.get("maximum_response_bytes") == scout.MAX_RAW_BYTES_PER_UTC_DAY
        and type(day.get("charged_response_bytes_including_prospective")) is int
        and 0
        <= day["charged_response_bytes_including_prospective"]
        <= scout.MAX_RAW_BYTES_PER_UTC_DAY
        and type(rolling) is dict
        and rolling.get("maximum_request_count") == scout.MAX_INTENTS_PER_ROLLING_30D
        and type(rolling.get("request_count_including_prospective")) is int
        and 1 <= rolling["request_count_including_prospective"] <= scout.MAX_INTENTS_PER_ROLLING_30D
        and rolling.get("maximum_response_bytes") == scout.MAX_RAW_BYTES_PER_ROLLING_30D
        and type(rolling.get("charged_response_bytes_including_prospective")) is int
        and 0
        <= rolling["charged_response_bytes_including_prospective"]
        <= scout.MAX_RAW_BYTES_PER_ROLLING_30D
        and type(admission.get("unresolved_attempt_count_in_rolling_30d")) is int
        and admission["unresolved_attempt_count_in_rolling_30d"] >= 1
    )


def expected_request(
    policy: Mapping[str, Any],
    binding_sha256: str,
    plan_record: Mapping[str, Any],
    slot_index: int,
) -> dict[str, Any]:
    """Rebuild a deterministic GET from a verified plan's exact selected slot."""
    _require(
        type(binding_sha256) is str and HASH_RE.fullmatch(binding_sha256) is not None,
        "v2_binding_hash_invalid",
    )
    selection = plan_record.get("selection")
    selected = selection.get("selected_pool_ids") if isinstance(selection, Mapping) else None
    _require(
        type(slot_index) is int and type(selected) is list and 0 <= slot_index < len(selected),
        "v2_selected_slot_invalid",
    )
    pool_id = cast(list[Any], selected)[slot_index]
    population = policy.get("population")
    _require(type(population) is list, "v2_population_invalid")
    pool = next((row for row in cast(list[Any], population) if row.get("pool_id") == pool_id), None)
    _require(type(pool) is dict, "v2_pool_not_in_population")
    pool = cast(dict[str, Any], pool)
    close_ns = plan_record.get("bucket_close_ns")
    _require(
        type(close_ns) is int and close_ns > 0 and close_ns % scout.BUCKET_NS == 0,
        "v2_bucket_close_invalid",
    )
    close_ns = cast(int, close_ns)
    close_s = close_ns // scout.NS
    path = f"/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute"
    query = {
        "aggregate": "5",
        "currency": "usd",
        "token": "base",
        "include_empty_intervals": "false",
        "limit": "6",
        "before_timestamp": str(close_s),
    }
    url = "https://api.geckoterminal.com" + path + "?" + urllib.parse.urlencode(query)
    headers = {"Accept": "application/json", "User-Agent": "brain-senses-gecko-capture-only-v2/1"}
    plan_hash = plan_record.get("record_sha256")
    plan_sequence = plan_record.get("sequence")
    core = {
        "binding_sha256": binding_sha256,
        "policy_sha256": policy.get("policy_sha256"),
        "plan_record_sha256": plan_hash,
        "plan_sequence": plan_sequence,
        "bucket_close_ns": close_ns,
        "slot_index": slot_index,
        "pool_id": pool_id,
    }
    request_id = sha256(canonical_json(core))
    semantic = {"method": "GET", "url": url, "headers": headers}
    return {
        "schema": "brain-senses-gecko-capture-only-v2-request-v1",
        "request_id": request_id,
        "method": "GET",
        "url": url,
        "path": path,
        "query": query,
        "headers": headers,
        "semantic_request_sha256": sha256(canonical_json(semantic)),
        **core,
        "maximum_response_bytes": MAX_RESPONSE_BYTES,
        "timeout_seconds": scout.HTTP_TIMEOUT_SECONDS,
    }


def build_intent(
    *,
    root: str,
    policy: Mapping[str, Any],
    binding: Mapping[str, Any],
    plan_record: Mapping[str, Any],
    slot_index: int,
    decision_ns: int,
    decision_monotonic_ns: int,
    clock_record: Mapping[str, Any],
    admission: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the immutable v2 intent after a fresh full-lane admission assessment."""
    binding_hash = str(binding.get("binding_sha256", ""))
    request = expected_request(policy, binding_hash, plan_record, slot_index)
    _require(type(decision_ns) is int and decision_ns > 0, "v2_decision_time_invalid")
    _require(
        type(decision_monotonic_ns) is int and decision_monotonic_ns >= 0,
        "v2_decision_monotonic_invalid",
    )
    admission_row = dict(admission)
    _require(
        _admission_is_bound(admission_row, request["request_id"], decision_ns),
        "v2_admission_not_for_request",
    )
    clock_bytes = canonical_json(dict(clock_record))
    core = {
        "schema": INTENT_SCHEMA,
        "request_id": request["request_id"],
        "request": request,
        "decision_ns": decision_ns,
        "decision_monotonic_ns": decision_monotonic_ns,
        "decision_clock": dict(clock_record),
        "decision_clock_sha256": sha256(clock_bytes),
        "admission": admission_row,
        "admission_sha256": sha256(canonical_json(admission_row)),
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    return {**core, "intent_sha256": sha256(canonical_json(core))}


def verify_capture_prefix_v2(
    *,
    root: str,
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    chain_records: Sequence[Mapping[str, Any]],
    intents: Sequence[Mapping[str, Any]],
    dispatch_records: Sequence[Mapping[str, Any]],
    terminals: Sequence[Mapping[str, Any]],
    raw_responses: Mapping[str, bytes],
    confirmations: Sequence[Mapping[str, Any]],
    coordinator_events: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Replay plan, timing, request, raw capture and durable confirmation projections."""
    _require(type(root) is str, "v2_root_invalid")
    root_path = PurePosixPath(root)
    _require(
        root_path.as_posix() == root
        and not root_path.is_absolute()
        and len(root_path.parts) == 4
        and root_path.parts[:3] == tuple(ROOT_PREFIX.split("/"))
        and re.fullmatch(r"[0-9a-f]{32}", root_path.parts[3]) is not None,
        "v2_root_invalid",
    )
    _require(type(binding) is dict and type(policy) is dict, "v2_binding_or_policy_shape")
    binding_core = dict(binding)
    claimed_binding_hash = binding_core.pop("binding_sha256", None)
    _require(
        type(claimed_binding_hash) is str
        and claimed_binding_hash == sha256(canonical_json(binding_core))
        and binding.get("root") == root
        and binding.get("schema") == f"{chain.SCHEMA}-binding"
        and binding.get("schema_version") == 1
        and binding.get("policy_sha256") == policy.get("policy_sha256")
        and binding.get("population_sha256") == policy.get("population_sha256")
        and binding.get("provider_io") is False
        and binding.get("activation_authorized") is False,
        "v2_binding_invalid",
    )
    storage = binding.get("storage")
    _require(
        type(storage) is dict
        and set(storage) == {"root_id", "volume_uuid", "device", "inode"}
        and type(storage.get("root_id")) is str
        and re.fullmatch(r"[0-9a-f]{32}", storage["root_id"]) is not None
        and type(storage.get("volume_uuid")) is str
        and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", storage["volume_uuid"])
        is not None
        and type(storage.get("device")) is int
        and storage["device"] >= 0
        and type(storage.get("inode")) is int
        and storage["inode"] > 0
        and type(binding.get("release_sha256")) is str
        and HASH_RE.fullmatch(binding["release_sha256"]) is not None,
        "v2_binding_storage_invalid",
    )
    try:
        chain.verify_chain(policy, chain_records)
    except (ValueError, KeyError, TypeError) as exc:
        raise VerifyError("v2_plan_chain_invalid") from exc
    plan_by_sequence = {
        row["sequence"]: row for row in chain_records if row.get("schema") == chain.PLAN_SCHEMA
    }
    _require(type(intents) in {list, tuple}, "v2_intents_shape")
    intent_by_id: dict[str, dict[str, Any]] = {}
    used_slots: set[tuple[int, int]] = set()
    usage_rows: list[dict[str, Any]] = []
    for raw_intent in intents:
        _require(type(raw_intent) is dict, "v2_intent_shape")
        intent = cast(dict[str, Any], raw_intent)
        expected_fields = {
            "schema",
            "request_id",
            "request",
            "decision_ns",
            "decision_monotonic_ns",
            "decision_clock",
            "decision_clock_sha256",
            "admission",
            "admission_sha256",
            "provider_charge_units",
            "provider_charge_status",
            "intent_sha256",
        }
        _require(set(intent) == expected_fields, "v2_intent_fields")
        request = intent.get("request")
        _require(type(request) is dict, "v2_request_shape")
        request = cast(dict[str, Any], request)
        sequence = request.get("plan_sequence")
        _require(type(sequence) is int, "v2_intent_plan_binding")
        plan = plan_by_sequence.get(sequence)
        _require(
            plan is not None and plan.get("record_sha256") == request.get("plan_record_sha256"),
            "v2_intent_plan_binding",
        )
        plan = cast(dict[str, Any], plan)
        slot_index = request.get("slot_index")
        _require(type(slot_index) is int, "v2_selected_slot_invalid")
        slot_index = cast(int, slot_index)
        try:
            expected = expected_request(policy, str(claimed_binding_hash), plan, slot_index)
        except (ValueError, TypeError, KeyError) as exc:
            raise VerifyError("v2_request_rebuild_failed") from exc
        _require(
            request == expected and intent.get("request_id") == expected["request_id"],
            "v2_request_mismatch",
        )
        close_ns = expected["bucket_close_ns"]
        decision_ns = intent.get("decision_ns")
        _require(
            type(decision_ns) is int
            and close_ns + WINDOW_START_NS <= decision_ns < close_ns + REQUEST_START_END_NS,
            "v2_intent_window",
        )
        decision_ns = cast(int, decision_ns)
        admission = intent.get("admission")
        _require(
            type(admission) is dict
            and intent.get("admission_sha256") == sha256(canonical_json(admission))
            and _admission_is_bound(admission, expected["request_id"], decision_ns),
            "v2_admission_binding",
        )
        admission = cast(dict[str, Any], admission)
        _record_hash(intent, "intent_sha256", "v2_intent_hash")
        clock_record = intent.get("decision_clock")
        _require(type(clock_record) is dict, "v2_clock_shape")
        clock_raw = canonical_json(clock_record)
        try:
            _require(sha256(clock_raw) == intent.get("decision_clock_sha256"), "v2_clock_hash")
            validate_progress(
                clock_record,
                wall_ns=decision_ns,
                monotonic_ns=intent["decision_monotonic_ns"],
            )
        except (ClockError, TypeError, json.JSONDecodeError) as exc:
            raise VerifyError("v2_clock_invalid") from exc
        _require(
            intent.get("provider_charge_units") is None
            and intent.get("provider_charge_status") == "unknown_unverified",
            "v2_provider_charge_claim",
        )
        slot_key = (cast(int, sequence), slot_index)
        _require(slot_key not in used_slots, "v2_duplicate_plan_slot")
        _require(intent["request_id"] not in intent_by_id, "v2_duplicate_request_id")
        used_slots.add(slot_key)
        intent_by_id[intent["request_id"]] = intent
        usage_rows.append(
            {
                "request_id": intent["request_id"],
                "decision_ns": decision_ns,
                "maximum_response_bytes": MAX_RESPONSE_BYTES,
                "response_bytes": None,
                "response_sha256": None,
            }
        )

    skip_slots: set[tuple[int, int]] = set()
    for skip in skips:
        _require(
            type(skip) is dict
            and set(skip)
            == {
                "schema",
                "plan_sequence",
                "slot_index",
                "pool_id",
                "bucket_close_ns",
                "at_ns",
                "reason",
            },
            "v2_skip_fields",
        )
        sequence = skip.get("plan_sequence")
        _require(type(sequence) is int, "v2_skip_semantics")
        plan = plan_by_sequence.get(sequence)
        slot = skip.get("slot_index")
        _require(
            plan is not None
            and type(slot) is int
            and skip.get("schema") == "brain-senses-gecko-capture-only-v2-skip-v1"
            and skip.get("reason") == "missed_dispatch"
            and skip.get("bucket_close_ns") == plan.get("bucket_close_ns")
            and type(skip.get("at_ns")) is int
            and skip["at_ns"] >= plan["bucket_close_ns"] + REQUEST_START_END_NS
            and type(plan.get("selection")) is dict
            and 0 <= slot < len(plan["selection"].get("selected_pool_ids", []))
            and skip.get("pool_id") == plan["selection"]["selected_pool_ids"][slot],
            "v2_skip_semantics",
        )
        key = (cast(int, sequence), cast(int, slot))
        _require(key not in used_slots and key not in skip_slots, "v2_skip_slot_duplicate")
        skip_slots.add(key)

    dispatch_by_id: dict[str, dict[str, Any]] = {}
    previous_hash: str | None = None
    previous_at: int | None = None
    dispatch_times: list[int] = []
    for index, row in enumerate(dispatch_records):
        _require(
            type(row) is dict
            and set(row)
            == {
                "schema",
                "sequence",
                "request_id",
                "intent_sha256",
                "dispatch_ns",
                "previous_sha256",
                "record_sha256",
            },
            "v2_dispatch_fields",
        )
        rid = row.get("request_id")
        _require(type(rid) is str, "v2_dispatch_chain")
        rid = cast(str, rid)
        found_intent = intent_by_id.get(rid)
        at = row.get("dispatch_ns")
        _require(
            row.get("schema") == DISPATCH_SCHEMA
            and row.get("sequence") == index
            and row.get("previous_sha256") == previous_hash
            and type(at) is int
            and found_intent is not None
            and row.get("intent_sha256") == found_intent.get("intent_sha256")
            and rid not in dispatch_by_id
            and (previous_at is None or at - previous_at >= MINIMUM_DISPATCH_SPACING_NS),
            "v2_dispatch_chain",
        )
        found_intent = cast(dict[str, Any], found_intent)
        close_ns = found_intent["request"]["bucket_close_ns"]
        _require(
            close_ns + WINDOW_START_NS <= at < close_ns + REQUEST_START_END_NS
            and at >= found_intent["decision_ns"],
            "v2_dispatch_window",
        )
        _record_hash(row, "record_sha256", "v2_dispatch_hash")
        previous_hash = cast(str, row["record_sha256"])
        previous_at = cast(int, at)
        dispatch_times.append(cast(int, at))
        dispatch_by_id[rid] = cast(dict[str, Any], row)
    left = 0
    for right, at in enumerate(dispatch_times):
        while at - dispatch_times[left] >= ROLLING_DISPATCH_WINDOW_NS:
            left += 1
        _require(right - left + 1 <= scout.ROLLING_MINUTE_DISPATCH_CAP, "v2_dispatch_rate")

    try:
        coordinator_raw = b"".join(capacity.canonical(dict(event)) for event in coordinator_events)
        verified_coordinator = capacity._verify_ledger(coordinator_raw)
    except (capacity.CoordinatorError, TypeError, ValueError) as exc:
        raise VerifyError("v2_coordinator_ledger_invalid") from exc
    reservations: dict[str, dict[str, Any]] = {}
    reservation_by_request: dict[str, dict[str, Any]] = {}
    for event in verified_coordinator:
        fields = event["fields"]
        if (
            event["kind"] == "reserved"
            and fields.get("service") == "gecko"
            and fields.get("intent_path", "").startswith(f"{root}/intents/")
        ):
            request_id = fields["request_id"]
            reservations[fields["reservation_id"]] = dict(event)
            reservation_by_request[request_id] = dict(event)
    coordinator_terminals: dict[str, dict[str, Any]] = {}
    for event in verified_coordinator:
        fields = event["fields"]
        if (
            event["kind"] in {"settled", "ambiguous_terminal", "released"}
            and event["fields"].get("reservation_id") in reservations
        ):
            coordinator_terminals[fields["reservation_id"]] = dict(event)
    _require(
        set(reservation_by_request) >= set(intent_by_id),
        "v2_coordinator_reservation_missing",
    )
    pending_requests = {
        event["fields"]["request_id"]
        for reservation_id, event in reservations.items()
        if reservation_id not in coordinator_terminals
    }
    _require(pending_requests <= set(intent_by_id), "v2_coordinator_orphan_reservation")
    for request_id, intent in intent_by_id.items():
        reservation_event = reservation_by_request[request_id]
        fields = reservation_event["fields"]
        payload_hash = sha256(canonical_json(intent))
        reservation_id = fields.get("reservation_id")
        coordinator_terminal = coordinator_terminals.get(reservation_id)
        _require(
            fields.get("intent_path") == f"{root}/intents/{request_id}.json"
            and fields.get("outcome_path") == f"{root}/results/{request_id}.json"
            and fields.get("intent_sha256") == payload_hash
            and fields.get("intent_bytes_sha256") == payload_hash
            and (coordinator_terminal is None or coordinator_terminal.get("kind") != "released"),
            "v2_coordinator_intent_binding",
        )

    terminal_by_id: dict[str, dict[str, Any]] = {}
    for row in terminals:
        _require(type(row) is dict, "v2_terminal_shape")
        rid = row.get("request_id")
        _require(type(rid) is str, "v2_terminal_binding")
        rid = cast(str, rid)
        found_intent = intent_by_id.get(rid)
        found_dispatch = dispatch_by_id.get(rid)
        _require(
            found_intent is not None and found_dispatch is not None and rid not in terminal_by_id,
            "v2_terminal_binding",
        )
        found_intent = cast(dict[str, Any], found_intent)
        found_dispatch = cast(dict[str, Any], found_dispatch)
        required = {
            "schema",
            "schema_version",
            "request_id",
            "intent_sha256",
            "plan_sha256",
            "dispatch_record_sha256",
            "dispatch_ns",
            "request_started_ns",
            "receipt_ns",
            "status",
            "http_status",
            "transport_error_class",
            "retry_after_seconds",
            "retry_after_header_count",
            "retry_after_evidence",
            "retry_after_class",
            "cooldown_until_ns",
            "stop_reason",
            "provider_io",
            "raw_body_b64",
            "response_bytes",
            "response_sha256",
            "bars",
            "provider_charge_units",
            "provider_charge_status",
        }
        _require(
            set(row) == required
            and row.get("schema") == TERMINAL_SCHEMA
            and row.get("schema_version") == 1
            and row.get("provider_io") is True,
            "v2_terminal_fields",
        )
        _require(
            row.get("intent_sha256") == found_intent.get("intent_sha256")
            and row.get("plan_sha256") == found_intent["request"].get("plan_record_sha256")
            and row.get("dispatch_record_sha256") == found_dispatch.get("record_sha256")
            and row.get("dispatch_ns") == found_dispatch.get("dispatch_ns")
            and row.get("provider_charge_units") is None
            and row.get("provider_charge_status") == "unknown_unverified",
            "v2_terminal_intent_binding",
        )
        started, received = row.get("request_started_ns"), row.get("receipt_ns")
        _require(
            type(started) is int
            and started >= row["dispatch_ns"]
            and started <= row["dispatch_ns"] + scout.HTTP_TIMEOUT_SECONDS * scout.NS
            and type(received) is int
            and received >= started
            and received <= started + scout.HTTP_TIMEOUT_SECONDS * scout.NS,
            "v2_terminal_times",
        )
        retry_seconds, retry_class = _retry_after_class(row)
        _require(
            row.get("retry_after_class") == retry_class
            and row.get("retry_after_seconds") == retry_seconds,
            "v2_retry_after_class_mismatch",
        )
        body_value = row.get("raw_body_b64")
        if body_value is None:
            body = None
        else:
            _require(type(body_value) is str, "v2_raw_body_encoding")
            try:
                body = base64.b64decode(body_value, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise VerifyError("v2_raw_body_encoding") from exc
            _require(base64.b64encode(body).decode("ascii") == body_value, "v2_raw_body_encoding")
        _require(
            raw_responses.get(rid) == body and (rid in raw_responses) == (body is not None),
            "v2_raw_body_file_projection",
        )
        status = row.get("status")
        if body is None:
            _require(
                row.get("response_bytes") is None and row.get("response_sha256") is None,
                "v2_terminal_raw_missing",
            )
            _require(
                row.get("bars") == [],
                "v2_terminal_raw_missing",
            )
        else:
            _require(
                type(body) is bytes
                and len(body) <= MAX_RESPONSE_BYTES
                and row.get("response_bytes") == len(body)
                and row.get("response_sha256") == sha256(body),
                "v2_raw_body_binding",
            )
            try:
                parsed = parse_response(body, found_intent["request"]["bucket_close_ns"], received)
            except (ValueError, TypeError, KeyError):
                _require(status == "malformed_response", "v2_malformed_response_status")
                parsed = []
            else:
                _require(status != "malformed_response", "v2_malformed_status_for_valid_response")
            expected_bars = []
            for bar in parsed:
                open_ns = bar["event_time_ns"]
                close_ns = found_intent["request"]["bucket_close_ns"]
                region = (
                    "new_open"
                    if close_ns - 900 * scout.NS <= open_ns < close_ns
                    else "overlap_revision"
                    if close_ns - 1800 * scout.NS <= open_ns < close_ns - 900 * scout.NS
                    else "out_of_window"
                )
                expected_bars.append(
                    {
                        key: bar[key]
                        for key in (
                            "open_s",
                            "event_time_ns",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                        )
                    }
                    | {"region": region}
                )
            _require(row.get("bars") == expected_bars, "v2_parsed_bars_mismatch")
            if status in {"received", "empty_response"}:
                _require(("received" if parsed else "empty_response") == status, "v2_parsed_status")
            usage = next(item for item in usage_rows if item["request_id"] == rid)
            usage["response_bytes"] = len(body)
            usage["response_sha256"] = sha256(body)
        _require(
            status
            in {
                "received",
                "empty_response",
                "provider_error",
                "rate_limited",
                "client_or_access_error",
                "malformed_response",
                "response_over_budget",
                "transport_uncertain_possibly_spent",
            },
            "v2_terminal_status",
        )
        if status in {"received", "empty_response"}:
            _require(body is not None, "v2_successful_result_body_missing")
        elif status in {"provider_error", "rate_limited", "client_or_access_error"}:
            _require(row.get("http_status") is not None, "v2_http_error_status_missing")
        else:
            _require(
                status in {"response_over_budget", "transport_uncertain_possibly_spent"},
                "v2_result_without_body_not_ambiguous",
            )
        _require(
            (body is None and row.get("bars") == [])
            or (body is not None and type(row.get("bars")) is list),
            "v2_terminal_bars_shape",
        )
        http_status = row.get("http_status")
        cooldown = row.get("cooldown_until_ns")
        stop_reason = row.get("stop_reason")
        if http_status == 429:
            expected_review = retry_class not in {"absent", "valid"}
            expected_stop = "review_required_retry_after" if expected_review else "rate_limited"
            expected_cooldown = (
                None
                if expected_review
                else received + max(3600, retry_seconds or 0) * scout.NS
            )
            _require(status == "rate_limited", "v2_rate_limit_status")
            _require(
                stop_reason == expected_stop and cooldown == expected_cooldown,
                "v2_rate_limit_policy",
            )
        elif http_status == 408 or (type(http_status) is int and http_status >= 500):
            expected_review = retry_class not in {"absent", "valid"}
            expected_stop = "review_required_retry_after" if expected_review else "provider_error"
            expected_cooldown = (
                None
                if expected_review
                else received + max(900, retry_seconds or 0) * scout.NS
            )
            _require(status == "provider_error", "v2_provider_error_status")
            _require(
                stop_reason == expected_stop and cooldown == expected_cooldown,
                "v2_provider_error_policy",
            )
        elif type(http_status) is int and 400 <= http_status < 500:
            _require(
                status == "client_or_access_error"
                and stop_reason == "client_or_access_error"
                and cooldown is None,
                "v2_access_error_policy",
            )
        elif status == "transport_uncertain_possibly_spent":
            _require(
                (http_status is None or row.get("transport_error_class") is not None)
                and stop_reason == "transport_uncertain"
                and cooldown == received + 900 * scout.NS,
                "v2_transport_cooldown",
            )
        elif status == "response_over_budget":
            _require(
                stop_reason == "response_over_budget" and cooldown is None,
                "v2_over_budget_policy",
            )
        else:
            _require(cooldown is None and stop_reason is None, "v2_unexpected_stop_policy")
        terminal_by_id[rid] = cast(dict[str, Any], row)
    _require(
        set(raw_responses)
        == {rid for rid, row in terminal_by_id.items() if row.get("response_sha256") is not None},
        "v2_raw_capture_closure",
    )

    confirmation_by_id: dict[str, dict[str, Any]] = {}
    confirmed_bars: dict[str, list[dict[str, Any]]] = {}
    for row in confirmations:
        _require(
            type(row) is dict
            and set(row)
            == {
                "schema",
                "schema_version",
                "request_id",
                "outcome_sha256",
                "availability_ns",
                "availability_monotonic_ns",
                "availability_clock",
                "availability_clock_sha256",
                "coordinator_event_sha256",
                "confirmation_sha256",
            },
            "v2_confirmation_fields",
        )
        rid = row.get("request_id")
        _require(type(rid) is str, "v2_confirmation_binding")
        rid = cast(str, rid)
        terminal = terminal_by_id.get(rid)
        _require(
            row.get("schema") == CONFIRMATION_SCHEMA
            and row.get("schema_version") == 1
            and terminal is not None
            and row.get("outcome_sha256") == sha256(canonical_json(terminal))
            and type(row.get("availability_ns")) is int
            and terminal.get("receipt_ns", 0) <= row["availability_ns"]
            and type(row.get("availability_monotonic_ns")) is int
            and row["availability_monotonic_ns"] >= 0
            and type(row.get("availability_clock")) is dict
            and HASH_RE.fullmatch(str(row.get("coordinator_event_sha256"))) is not None,
            "v2_confirmation_binding",
        )
        terminal = cast(dict[str, Any], terminal)
        _record_hash(row, "confirmation_sha256", "v2_confirmation_hash")
        clock_record = cast(dict[str, Any], row["availability_clock"])
        clock_raw = canonical_json(clock_record)
        try:
            _require(
                sha256(clock_raw) == row.get("availability_clock_sha256"),
                "v2_confirmation_clock_hash",
            )
            validate_progress(
                clock_record,
                wall_ns=row["availability_ns"],
                monotonic_ns=row["availability_monotonic_ns"],
            )
        except (ClockError, TypeError, json.JSONDecodeError) as exc:
            raise VerifyError("v2_confirmation_clock_invalid") from exc
        reservation_event = reservation_by_request[rid]
        reservation_id = reservation_event["fields"]["reservation_id"]
        coordinator_terminal = coordinator_terminals.get(reservation_id)
        _require(
            reservation_event["fields"].get("reservation_id") == reservation_id
            and coordinator_terminal is not None
            and coordinator_terminal.get("kind") in {"settled", "ambiguous_terminal"}
            and coordinator_terminal["fields"].get("reservation_id") == reservation_id
            and row.get("coordinator_event_sha256") == coordinator_terminal.get("event_sha256")
            and coordinator_terminal["fields"].get("outcome_sha256")
            == sha256(canonical_json(terminal)),
            "v2_confirmation_coordinator_binding",
        )
        coordinator_terminal = cast(dict[str, Any], coordinator_terminal)
        _require(
            coordinator_terminal["at_ns"] <= row["availability_ns"],
            "v2_confirmation_availability_order",
        )
        body = raw_responses.get(rid)
        if (
            body is not None
            and terminal.get("status") in {"received", "empty_response"}
            and terminal.get("request_started_ns")
            < intent_by_id[rid]["request"]["bucket_close_ns"] + REQUEST_START_END_NS
            and row["availability_ns"]
            < intent_by_id[rid]["request"]["bucket_close_ns"] + AVAILABILITY_END_NS
        ):
            try:
                parsed = parse_response(
                    body,
                    intent_by_id[rid]["request"]["bucket_close_ns"],
                    row["availability_ns"],
                )
                close_ns = intent_by_id[rid]["request"]["bucket_close_ns"]
                if any(
                    close_ns - 900 * scout.NS <= bar["event_time_ns"] < close_ns for bar in parsed
                ):
                    confirmed_bars[rid] = parsed
            except (ValueError, TypeError, KeyError) as exc:
                raise VerifyError("v2_confirmed_response_unparseable") from exc
        confirmation_by_id[rid] = cast(dict[str, Any], row)
    _require(set(confirmation_by_id) <= set(terminal_by_id), "v2_orphan_confirmation")
    _require(set(dispatch_by_id) <= set(intent_by_id), "v2_orphan_dispatch")
    _require(set(terminal_by_id) <= set(dispatch_by_id), "v2_orphan_terminal")
    for request_id, terminal in terminal_by_id.items():
        reservation = reservation_by_request[request_id]
        reservation_id = reservation["fields"]["reservation_id"]
        coordinator_terminal = coordinator_terminals.get(reservation_id)
        if coordinator_terminal is not None:
            _require(
                coordinator_terminal.get("kind") in {"settled", "ambiguous_terminal"}
                and coordinator_terminal["fields"].get("outcome_sha256")
                == sha256(canonical_json(terminal)),
                "v2_coordinator_outcome_mismatch",
            )
    return {
        "status": "verified_prefix",
        "plan_count": len(plan_by_sequence),
        "intent_count": len(intent_by_id),
        "dispatch_count": len(dispatch_by_id),
        "terminal_count": len(terminal_by_id),
        "confirmation_count": len(confirmation_by_id),
        "confirmed_eligible_request_count": len(confirmed_bars),
        "unresolved_count": len(intent_by_id) - len(terminal_by_id),
        "usage_attempts": sorted(
            usage_rows, key=lambda row: (row["decision_ns"], row["request_id"])
        ),
        "provider_io": False,
        "activation_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }


__all__ = [
    "CONFIRMATION_SCHEMA",
    "DISPATCH_SCHEMA",
    "INTENT_SCHEMA",
    "ROOT_PREFIX",
    "TERMINAL_SCHEMA",
    "VerifyError",
    "build_intent",
    "expected_request",
    "verify_capture_prefix_v2",
]
