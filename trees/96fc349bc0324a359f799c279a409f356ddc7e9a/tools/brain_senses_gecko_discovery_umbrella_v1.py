"""Pure 45-day, 15-epoch Gecko discovery lease and handoff policy.

No filesystem, provider, credential, process, or launchd operations occur here.
Every epoch transition carries the full prior accounting state and requires an
independently replayed terminal seal before the next epoch can dispatch.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "brain-senses-gecko-discovery-umbrella-v1"
STATE_SCHEMA = f"{SCHEMA}-state"
PREPARE_SCHEMA = f"{SCHEMA}-prepare"
SEAL_SCHEMA = f"{SCHEMA}-terminal-seal"
TRANSFER_SCHEMA = f"{SCHEMA}-transfer"
DECISION_SCHEMA = f"{SCHEMA}-decision"
VERSION = 1
NS = 1_000_000_000
SCAN_INTERVAL_NS = 300 * NS
SCANS_PER_EPOCH = 864
EPOCH_NS = 72 * 60 * 60 * NS
EPOCH_COUNT = 15
UMBRELLA_NS = 45 * 24 * 60 * 60 * NS
STANDBY_NS = 60 * 60 * NS
MAX_REQUESTS_PER_SCAN = 3
MAX_REQUESTS_TOTAL = 38_880
MAX_REQUESTS_PER_DAY = 864
MAX_REQUESTS_PER_ROLLING_30D = 25_920
MAX_REQUESTS_PER_ROLLING_60S = 5
MAX_RAW_BYTES_PER_REQUEST = 512 * 1024
MAX_RAW_BYTES_TOTAL = 20_384_317_440
MAX_PHYSICAL_BYTES_PER_REQUEST = 5 * MAX_RAW_BYTES_PER_REQUEST
MAX_PHYSICAL_BYTES_TOTAL = 101_921_587_200
MAX_RAW_BYTES_PER_DAY = MAX_REQUESTS_PER_DAY * MAX_RAW_BYTES_PER_REQUEST
MAX_PHYSICAL_BYTES_PER_DAY = MAX_REQUESTS_PER_DAY * MAX_PHYSICAL_BYTES_PER_REQUEST
MAX_PHYSICAL_BYTES_PER_ROLLING_30D = MAX_REQUESTS_PER_ROLLING_30D * MAX_PHYSICAL_BYTES_PER_REQUEST
ROLLING_30D_NS = 30 * 24 * 60 * 60 * NS
DAY_NS = 24 * 60 * 60 * NS
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"[0-9a-f]{32}\Z")


class UmbrellaError(ValueError):
    """Malformed lease state, accounting, or epoch handoff."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _need(value: bool, reason: str) -> None:
    if not value:
        raise UmbrellaError(reason)


def _hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _id(value: Any) -> bool:
    return type(value) is str and ID_RE.fullmatch(value) is not None


def _uint(value: Any) -> bool:
    return type(value) is int and value >= 0


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _exact_map(value: Any, keys: set[str], reason: str) -> Mapping[str, Any]:
    _need(type(value) is dict and set(value) == keys, reason)
    return value


def _state_core(state: Mapping[str, Any]) -> dict[str, Any]:
    core = dict(state)
    core.pop("state_sha256", None)
    return core


def _seal_state(core: dict[str, Any]) -> dict[str, Any]:
    return {**core, "state_sha256": sha256(canonical(core))}


def _derive_accounting(requests: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "request_count": len(requests),
        "raw_bytes_upper_bound": sum(row["raw_bytes_upper_bound"] for row in requests),
        "physical_bytes_upper_bound": sum(row["physical_bytes_upper_bound"] for row in requests),
        "ambiguous_request_count": sum(
            row["status"] == "ambiguous_unclassified" for row in requests
        ),
        "ambiguous_raw_debt_bytes": sum(
            row["raw_bytes_upper_bound"]
            for row in requests
            if row["status"] == "ambiguous_unclassified"
        ),
        "ambiguous_physical_debt_bytes": sum(
            row["physical_bytes_upper_bound"]
            for row in requests
            if row["status"] == "ambiguous_unclassified"
        ),
    }


def _request_shape(raw: Any, *, scan_index: int, scheduled_ns: int) -> dict[str, Any]:
    _need(type(raw) is dict, "umbrella_request_shape")
    status = raw.get("status")
    common = {"request_id", "dispatch_ns", "status"}
    if status == "confirmed":
        expected = common | {"raw_bytes"}
    elif status == "ambiguous_unclassified":
        expected = common | {"classification"}
    elif status == "classified_transient":
        expected = common | {"classification_sha256", "cooldown_until_ns"}
    elif status == "fatal_stop":
        expected = common | {"classification"}
    else:
        raise UmbrellaError("umbrella_request_status")
    request = dict(_exact_map(raw, expected, "umbrella_request_shape"))
    _need(
        _hash(request.get("request_id"))
        and _positive(request.get("dispatch_ns"))
        and scheduled_ns <= request["dispatch_ns"] < scheduled_ns + SCAN_INTERVAL_NS,
        "umbrella_request_identity_time",
    )
    if status == "confirmed":
        _need(
            type(request.get("raw_bytes")) is int
            and 0 <= request["raw_bytes"] <= MAX_RAW_BYTES_PER_REQUEST,
            "umbrella_confirmed_raw_size",
        )
        raw_bound = request["raw_bytes"]
    elif status == "ambiguous_unclassified":
        _need(
            request.get("classification") == "prior_ambiguity_unclassified",
            "umbrella_ambiguity_classification",
        )
        raw_bound = MAX_RAW_BYTES_PER_REQUEST
    elif status == "classified_transient":
        _need(
            _hash(request.get("classification_sha256"))
            and _positive(request.get("cooldown_until_ns"))
            and request["cooldown_until_ns"] >= request["dispatch_ns"],
            "umbrella_transient_classification",
        )
        raw_bound = MAX_RAW_BYTES_PER_REQUEST
    else:
        _need(request.get("classification") == "fatal_fail_closed", "umbrella_fatal_classification")
        raw_bound = MAX_RAW_BYTES_PER_REQUEST
    return {
        **request,
        "scan_index": scan_index,
        "raw_bytes_upper_bound": raw_bound,
        "physical_bytes_upper_bound": MAX_PHYSICAL_BYTES_PER_REQUEST,
    }


def _verify_state(state: Any) -> dict[str, Any]:
    state_keys = {
        "schema",
        "schema_version",
        "umbrella_id",
        "release_sha256",
        "source_sha256",
        "policy_sha256",
        "start_ns",
        "end_ns",
        "epoch_index",
        "epoch_start_ns",
        "epoch_end_ns",
        "lease_id",
        "root_id",
        "epoch_history",
        "prior_seal_sha256",
        "phase",
        "next_scan_index",
        "scan_records",
        "requests",
        "stop_latched",
        "cooldown_until_ns",
        "accounting",
        "state_sha256",
    }
    mapping = _exact_map(state, state_keys, "umbrella_state_shape")
    core = _state_core(mapping)
    _need(
        mapping.get("schema") == STATE_SCHEMA
        and mapping.get("schema_version") == VERSION
        and _id(mapping.get("umbrella_id"))
        and all(
            _hash(mapping.get(name))
            for name in ("release_sha256", "source_sha256", "policy_sha256")
        )
        and _positive(mapping.get("start_ns"))
        and mapping["start_ns"] % SCAN_INTERVAL_NS == 0
        and mapping.get("end_ns") == mapping["start_ns"] + UMBRELLA_NS
        and type(mapping.get("epoch_index")) is int
        and 0 <= mapping["epoch_index"] < EPOCH_COUNT
        and mapping.get("epoch_start_ns") == mapping["start_ns"] + mapping["epoch_index"] * EPOCH_NS
        and mapping.get("epoch_end_ns") == mapping["epoch_start_ns"] + EPOCH_NS
        and _id(mapping.get("lease_id"))
        and _id(mapping.get("root_id"))
        and mapping.get("phase") in {"active", "prepared", "transferred"}
        and (mapping.get("prior_seal_sha256") is None or _hash(mapping.get("prior_seal_sha256")))
        and type(mapping.get("next_scan_index")) is int
        and mapping["epoch_index"] * SCANS_PER_EPOCH
        <= mapping["next_scan_index"]
        <= (mapping["epoch_index"] + 1) * SCANS_PER_EPOCH
        and type(mapping.get("stop_latched")) is bool
        and (
            mapping.get("cooldown_until_ns") is None or _positive(mapping.get("cooldown_until_ns"))
        )
        and _hash(mapping.get("state_sha256"))
        and sha256(canonical(core)) == mapping["state_sha256"],
        "umbrella_state_binding",
    )
    history = mapping.get("epoch_history")
    _need(
        type(history) is list and len(history) == mapping["epoch_index"] + 1,
        "umbrella_history_shape",
    )
    _need(
        history[-1]
        == {
            "epoch_index": mapping["epoch_index"],
            "lease_id": mapping["lease_id"],
            "root_id": mapping["root_id"],
            "epoch_start_ns": mapping["epoch_start_ns"],
            "epoch_end_ns": mapping["epoch_end_ns"],
        },
        "umbrella_history_current_identity",
    )
    _need(
        all(
            type(row) is dict
            and set(row) == {"epoch_index", "lease_id", "root_id", "epoch_start_ns", "epoch_end_ns"}
            and row.get("epoch_index") == index
            and row.get("epoch_start_ns") == mapping["start_ns"] + index * EPOCH_NS
            and row.get("epoch_end_ns") == row["epoch_start_ns"] + EPOCH_NS
            and _id(row.get("lease_id"))
            and _id(row.get("root_id"))
            for index, row in enumerate(history)
        ),
        "umbrella_history_row",
    )
    _need(
        len({row["lease_id"] for row in history}) == len(history)
        and len({row["root_id"] for row in history}) == len(history),
        "umbrella_epoch_identity_reused",
    )
    _need(
        (mapping["epoch_index"] == 0 and mapping.get("prior_seal_sha256") is None)
        or (mapping["epoch_index"] > 0 and _hash(mapping.get("prior_seal_sha256"))),
        "umbrella_prior_seal_binding",
    )
    scans = mapping.get("scan_records")
    requests = mapping.get("requests")
    _need(
        type(scans) is list and len(scans) == mapping["next_scan_index"],
        "umbrella_scan_denominator",
    )
    _need(type(requests) is list, "umbrella_request_list")
    request_ids: set[str] = set()
    request_by_scan: dict[int, list[dict[str, Any]]] = {}
    for event in requests:
        _need(type(event) is dict, "umbrella_request_row")
        _need(
            _hash(event.get("request_id")) and event["request_id"] not in request_ids,
            "umbrella_request_duplicate",
        )
        request_ids.add(event["request_id"])
        _need(
            type(event.get("scan_index")) is int and 0 <= event["scan_index"] < len(scans),
            "umbrella_request_scan",
        )
        scheduled_ns = mapping["start_ns"] + event["scan_index"] * SCAN_INTERVAL_NS
        request_status = event.get("status")
        raw_keys = {"request_id", "dispatch_ns", "status"}
        if request_status == "confirmed":
            raw_keys.add("raw_bytes")
        elif request_status == "ambiguous_unclassified":
            raw_keys.add("classification")
        elif request_status == "classified_transient":
            raw_keys.update({"classification_sha256", "cooldown_until_ns"})
        elif request_status == "fatal_stop":
            raw_keys.add("classification")
        else:
            raise UmbrellaError("umbrella_request_status")
        raw = {key: value for key, value in event.items() if key in raw_keys}
        rebuilt = _request_shape(raw, scan_index=event["scan_index"], scheduled_ns=scheduled_ns)
        _need(event == rebuilt, "umbrella_request_accounting_fields")
        request_by_scan.setdefault(event["scan_index"], []).append(event)
    gap_count = 0
    for index, row in enumerate(scans):
        _need(type(row) is dict and row.get("scan_index") == index, "umbrella_scan_order")
        scheduled = mapping["start_ns"] + index * SCAN_INTERVAL_NS
        _need(row.get("scheduled_ns") == scheduled, "umbrella_scan_schedule")
        events = request_by_scan.get(index, [])
        if row.get("status") == "gap":
            _need(
                set(row) == {"scan_index", "scheduled_ns", "status", "reason", "request_ids"}
                and type(row.get("reason")) is str
                and row["reason"]
                and row.get("request_ids") == []
                and events == [],
                "umbrella_gap_shape",
            )
            gap_count += 1
        else:
            _need(
                row.get("status") == "scanned"
                and set(row) == {"scan_index", "scheduled_ns", "status", "request_ids"}
                and type(row.get("request_ids")) is list
                and 1 <= len(row["request_ids"]) <= MAX_REQUESTS_PER_SCAN
                and row["request_ids"] == [event["request_id"] for event in events],
                "umbrella_scanned_shape",
            )
    accounting = _derive_accounting(requests)
    _need(
        mapping.get("accounting") == {**accounting, "gap_count": gap_count},
        "umbrella_accounting_mismatch",
    )
    _need(
        accounting["request_count"] <= MAX_REQUESTS_TOTAL
        and accounting["raw_bytes_upper_bound"] <= MAX_RAW_BYTES_TOTAL
        and accounting["physical_bytes_upper_bound"] <= MAX_PHYSICAL_BYTES_TOTAL,
        "umbrella_lifetime_cap",
    )
    _verify_rate_caps(requests)
    _need(
        mapping["stop_latched"]
        == any(event["status"] in {"ambiguous_unclassified", "fatal_stop"} for event in requests),
        "umbrella_stop_latch_mismatch",
    )
    expected_cooldown = max(
        (
            event["cooldown_until_ns"]
            for event in requests
            if event["status"] == "classified_transient"
        ),
        default=None,
    )
    _need(mapping.get("cooldown_until_ns") == expected_cooldown, "umbrella_cooldown_mismatch")
    return dict(mapping)


def _verify_rate_caps(requests: Sequence[Mapping[str, Any]]) -> None:
    from collections import deque

    ordered = sorted(requests, key=lambda row: row["dispatch_ns"])
    _need(
        [row["dispatch_ns"] for row in ordered] == sorted(row["dispatch_ns"] for row in requests),
        "umbrella_dispatch_order",
    )
    per_day_requests: dict[int, int] = {}
    per_day_raw: dict[int, int] = {}
    per_day_physical: dict[int, int] = {}
    minute_window: deque[Mapping[str, Any]] = deque()
    month_window: deque[Mapping[str, Any]] = deque()
    month_raw = 0
    month_physical = 0
    for row in ordered:
        day = row["dispatch_ns"] // DAY_NS
        per_day_requests[day] = per_day_requests.get(day, 0) + 1
        per_day_raw[day] = per_day_raw.get(day, 0) + row["raw_bytes_upper_bound"]
        per_day_physical[day] = per_day_physical.get(day, 0) + row["physical_bytes_upper_bound"]
        _need(per_day_requests[day] <= MAX_REQUESTS_PER_DAY, "umbrella_daily_request_cap")
        _need(per_day_raw[day] <= MAX_RAW_BYTES_PER_DAY, "umbrella_daily_raw_cap")
        _need(per_day_physical[day] <= MAX_PHYSICAL_BYTES_PER_DAY, "umbrella_daily_physical_cap")
        while minute_window and row["dispatch_ns"] - minute_window[0]["dispatch_ns"] >= 60 * NS:
            minute_window.popleft()
        minute_window.append(row)
        _need(len(minute_window) <= MAX_REQUESTS_PER_ROLLING_60S, "umbrella_rolling_60s_cap")
        while (
            month_window and row["dispatch_ns"] - month_window[0]["dispatch_ns"] >= ROLLING_30D_NS
        ):
            expired = month_window.popleft()
            month_raw -= expired["raw_bytes_upper_bound"]
            month_physical -= expired["physical_bytes_upper_bound"]
        month_window.append(row)
        month_raw += row["raw_bytes_upper_bound"]
        month_physical += row["physical_bytes_upper_bound"]
        _need(len(month_window) <= MAX_REQUESTS_PER_ROLLING_30D, "umbrella_rolling_30d_request_cap")
        _need(month_raw <= MAX_RAW_BYTES_TOTAL, "umbrella_rolling_30d_raw_cap")
        _need(
            month_physical <= MAX_PHYSICAL_BYTES_PER_ROLLING_30D,
            "umbrella_rolling_30d_physical_cap",
        )
    _need(len(ordered) <= MAX_REQUESTS_TOTAL, "umbrella_total_request_cap")


def build_umbrella(
    *,
    umbrella_id: str,
    release_sha256: str,
    source_sha256: str,
    policy_sha256: str,
    start_ns: int,
    lease_id: str,
    root_id: str,
) -> dict[str, Any]:
    """Freeze the 45-day envelope and its first immutable epoch identity."""
    _need(
        _id(umbrella_id)
        and all(_hash(value) for value in (release_sha256, source_sha256, policy_sha256))
        and _positive(start_ns)
        and start_ns % SCAN_INTERVAL_NS == 0
        and _id(lease_id)
        and _id(root_id),
        "umbrella_genesis_input",
    )
    core = {
        "schema": STATE_SCHEMA,
        "schema_version": VERSION,
        "umbrella_id": umbrella_id,
        "release_sha256": release_sha256,
        "source_sha256": source_sha256,
        "policy_sha256": policy_sha256,
        "start_ns": start_ns,
        "end_ns": start_ns + UMBRELLA_NS,
        "epoch_index": 0,
        "epoch_start_ns": start_ns,
        "epoch_end_ns": start_ns + EPOCH_NS,
        "lease_id": lease_id,
        "root_id": root_id,
        "epoch_history": [
            {
                "epoch_index": 0,
                "lease_id": lease_id,
                "root_id": root_id,
                "epoch_start_ns": start_ns,
                "epoch_end_ns": start_ns + EPOCH_NS,
            }
        ],
        "prior_seal_sha256": None,
        "phase": "active",
        "next_scan_index": 0,
        "scan_records": [],
        "requests": [],
        "stop_latched": False,
        "cooldown_until_ns": None,
        "accounting": {
            "request_count": 0,
            "raw_bytes_upper_bound": 0,
            "physical_bytes_upper_bound": 0,
            "ambiguous_request_count": 0,
            "ambiguous_raw_debt_bytes": 0,
            "ambiguous_physical_debt_bytes": 0,
            "gap_count": 0,
        },
    }
    state = _seal_state(core)
    _verify_state(state)
    return state


def verify_state(state: Any) -> dict[str, Any]:
    """Public full replay of scan, request, rate, debt, and lease state."""
    return _verify_state(state)


def record_scans(state: Any, scans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Append contiguous scan outcomes while preserving all prior counters/debt."""
    current = _verify_state(state)
    _need(current["phase"] in {"active", "transferred"}, "umbrella_scan_phase")
    _need(not current["stop_latched"], "umbrella_stop_latched")
    _need(type(scans) in {list, tuple} and len(scans) > 0, "umbrella_scan_batch")
    rows = list(current["scan_records"])
    requests = list(current["requests"])
    request_ids = {row["request_id"] for row in requests}
    stop_latched = current["stop_latched"]
    cooldown_until = current["cooldown_until_ns"]
    index = current["next_scan_index"]
    for raw_scan in scans:
        _need(
            type(raw_scan) is dict and index < EPOCH_COUNT * SCANS_PER_EPOCH,
            "umbrella_scan_exhausted",
        )
        _need(
            index < (current["epoch_index"] + 1) * SCANS_PER_EPOCH, "umbrella_epoch_scan_exhausted"
        )
        scheduled_ns = current["start_ns"] + index * SCAN_INTERVAL_NS
        _need(scheduled_ns < current["end_ns"], "umbrella_after_day45")
        if raw_scan.get("status") == "gap":
            gap = _exact_map(raw_scan, {"status", "reason"}, "umbrella_gap_input")
            _need(type(gap.get("reason")) is str and gap["reason"], "umbrella_gap_reason")
            rows.append(
                {
                    "scan_index": index,
                    "scheduled_ns": scheduled_ns,
                    "status": "gap",
                    "reason": gap["reason"],
                    "request_ids": [],
                }
            )
        else:
            scan = _exact_map(raw_scan, {"status", "requests"}, "umbrella_scan_input")
            _need(
                scan.get("status") == "scanned" and type(scan.get("requests")) is list,
                "umbrella_scan_input",
            )
            _need(
                1 <= len(scan["requests"]) <= MAX_REQUESTS_PER_SCAN, "umbrella_scan_request_count"
            )
            normalized = []
            for raw_request in scan["requests"]:
                request = _request_shape(raw_request, scan_index=index, scheduled_ns=scheduled_ns)
                _need(request["dispatch_ns"] < current["end_ns"], "umbrella_after_day45")
                _need(request["request_id"] not in request_ids, "umbrella_request_replay")
                _need(request["dispatch_ns"] >= (cooldown_until or 0), "umbrella_cooldown_active")
                _need(not stop_latched, "umbrella_stop_latched")
                request_ids.add(request["request_id"])
                requests.append(request)
                normalized.append(request)
                if request["status"] == "classified_transient":
                    cooldown_until = max(cooldown_until or 0, request["cooldown_until_ns"])
                elif request["status"] in {"ambiguous_unclassified", "fatal_stop"}:
                    stop_latched = True
            rows.append(
                {
                    "scan_index": index,
                    "scheduled_ns": scheduled_ns,
                    "status": "scanned",
                    "request_ids": [row["request_id"] for row in normalized],
                }
            )
        index += 1
    accounting = _derive_accounting(requests)
    accounting["gap_count"] = sum(row["status"] == "gap" for row in rows)
    _need(
        accounting["request_count"] <= MAX_REQUESTS_TOTAL
        and accounting["raw_bytes_upper_bound"] <= MAX_RAW_BYTES_TOTAL
        and accounting["physical_bytes_upper_bound"] <= MAX_PHYSICAL_BYTES_TOTAL,
        "umbrella_lifetime_cap",
    )
    _verify_rate_caps(requests)
    core = _state_core(current)
    core.update(
        {
            "phase": "active",
            "next_scan_index": index,
            "scan_records": rows,
            "requests": requests,
            "stop_latched": stop_latched,
            "cooldown_until_ns": cooldown_until,
            "accounting": accounting,
        }
    )
    next_state = _seal_state(core)
    _verify_state(next_state)
    return next_state


def standby_due(state: Any, now_ns: int) -> bool:
    current = _verify_state(state)
    _need(_positive(now_ns), "umbrella_now_ns")
    return now_ns >= current["epoch_end_ns"] - STANDBY_NS


def prepare_handoff(
    state: Any,
    *,
    successor_lease_id: str,
    successor_root_id: str,
    now_ns: int,
) -> dict[str, Any]:
    """Create deterministic standby metadata for one distinct successor identity."""
    current = _verify_state(state)
    _need(current["epoch_index"] < EPOCH_COUNT - 1, "umbrella_no_successor_epoch")
    _need(standby_due(current, now_ns), "umbrella_standby_not_due")
    _need(not current["stop_latched"], "umbrella_stop_latched")
    _need(
        _id(successor_lease_id)
        and _id(successor_root_id)
        and successor_lease_id not in {row["lease_id"] for row in current["epoch_history"]}
        and successor_root_id not in {row["root_id"] for row in current["epoch_history"]},
        "umbrella_successor_identity_reused",
    )
    epoch_start = current["epoch_end_ns"]
    core = {
        "schema": PREPARE_SCHEMA,
        "schema_version": VERSION,
        "umbrella_id": current["umbrella_id"],
        "from_epoch": current["epoch_index"],
        "to_epoch": current["epoch_index"] + 1,
        "predecessor_lease_id": current["lease_id"],
        "predecessor_root_id": current["root_id"],
        "successor_lease_id": successor_lease_id,
        "successor_root_id": successor_root_id,
        "epoch_start_ns": epoch_start,
        "epoch_end_ns": epoch_start + EPOCH_NS,
        "standby_due_ns": current["epoch_end_ns"] - STANDBY_NS,
        "prepared_at_ns": now_ns,
        "release_sha256": current["release_sha256"],
        "source_sha256": current["source_sha256"],
        "policy_sha256": current["policy_sha256"],
        "prepared_state_sha256": current["state_sha256"],
        "prepared_accounting": current["accounting"],
        "prepared_scan_count": current["next_scan_index"],
        "prepared_request_count": current["accounting"]["request_count"],
        "prepared_request_prefix_sha256": sha256(canonical(current["requests"])),
        "prepared_scan_prefix_sha256": sha256(canonical(current["scan_records"])),
        "dispatch_allowed": False,
    }
    return {**core, "prepare_sha256": sha256(canonical(core))}


def _verify_prepare(prepared: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    prepare_keys = {
        "schema",
        "schema_version",
        "umbrella_id",
        "from_epoch",
        "to_epoch",
        "predecessor_lease_id",
        "predecessor_root_id",
        "successor_lease_id",
        "successor_root_id",
        "epoch_start_ns",
        "epoch_end_ns",
        "standby_due_ns",
        "prepared_at_ns",
        "release_sha256",
        "source_sha256",
        "policy_sha256",
        "prepared_state_sha256",
        "prepared_accounting",
        "prepared_scan_count",
        "prepared_request_count",
        "prepared_request_prefix_sha256",
        "prepared_scan_prefix_sha256",
        "dispatch_allowed",
        "prepare_sha256",
    }
    record = dict(_exact_map(prepared, prepare_keys, "umbrella_prepare_shape"))
    core = dict(record)
    digest = core.pop("prepare_sha256")
    _need(_hash(digest) and sha256(canonical(core)) == digest, "umbrella_prepare_hash")
    _need(
        record.get("schema") == PREPARE_SCHEMA
        and record.get("schema_version") == VERSION
        and record.get("umbrella_id") == state["umbrella_id"]
        and record.get("from_epoch") == state["epoch_index"]
        and record.get("to_epoch") == state["epoch_index"] + 1
        and record.get("predecessor_lease_id") == state["lease_id"]
        and record.get("predecessor_root_id") == state["root_id"]
        and record.get("epoch_start_ns") == state["epoch_end_ns"]
        and record.get("epoch_end_ns") == state["epoch_end_ns"] + EPOCH_NS
        and record.get("release_sha256") == state["release_sha256"]
        and record.get("source_sha256") == state["source_sha256"]
        and record.get("policy_sha256") == state["policy_sha256"]
        and record.get("dispatch_allowed") is False,
        "umbrella_prepare_binding",
    )
    _need(
        _hash(record.get("prepared_state_sha256"))
        and _hash(record.get("prepared_request_prefix_sha256"))
        and _hash(record.get("prepared_scan_prefix_sha256"))
        and type(record.get("prepared_accounting")) is dict
        and set(record["prepared_accounting"])
        == {
            "request_count",
            "raw_bytes_upper_bound",
            "physical_bytes_upper_bound",
            "ambiguous_request_count",
            "ambiguous_raw_debt_bytes",
            "ambiguous_physical_debt_bytes",
            "gap_count",
        }
        and all(_uint(value) for value in record["prepared_accounting"].values())
        and type(record.get("prepared_scan_count")) is int
        and 0 <= record["prepared_scan_count"] <= state["next_scan_index"]
        and type(record.get("prepared_request_count")) is int
        and record["prepared_request_count"] == record["prepared_accounting"]["request_count"]
        and record["prepared_request_count"] <= state["accounting"]["request_count"]
        and record["prepared_accounting"]["raw_bytes_upper_bound"]
        <= state["accounting"]["raw_bytes_upper_bound"]
        and record["prepared_accounting"]["physical_bytes_upper_bound"]
        <= state["accounting"]["physical_bytes_upper_bound"]
        and record["prepared_accounting"]["gap_count"] <= state["accounting"]["gap_count"]
        and record["prepared_accounting"]["ambiguous_request_count"]
        <= state["accounting"]["ambiguous_request_count"]
        and record["prepared_accounting"]["ambiguous_raw_debt_bytes"]
        <= state["accounting"]["ambiguous_raw_debt_bytes"]
        and record["prepared_accounting"]["ambiguous_physical_debt_bytes"]
        <= state["accounting"]["ambiguous_physical_debt_bytes"]
        and _positive(record.get("prepared_at_ns"))
        and record["prepared_at_ns"] >= record["standby_due_ns"]
        and record["standby_due_ns"] == state["epoch_end_ns"] - STANDBY_NS
        and _id(record.get("successor_lease_id"))
        and _id(record.get("successor_root_id"))
        and record["successor_lease_id"] not in {row["lease_id"] for row in state["epoch_history"]}
        and record["successor_root_id"] not in {row["root_id"] for row in state["epoch_history"]},
        "umbrella_prepare_prefix_hashes",
    )
    _need(
        record["prepared_request_prefix_sha256"]
        == sha256(canonical(state["requests"][: record["prepared_request_count"]]))
        and record["prepared_scan_prefix_sha256"]
        == sha256(canonical(state["scan_records"][: record["prepared_scan_count"]]))
        and record["prepared_accounting"]
        == {
            **_derive_accounting(state["requests"][: record["prepared_request_count"]]),
            "gap_count": sum(
                row["status"] == "gap"
                for row in state["scan_records"][: record["prepared_scan_count"]]
            ),
        },
        "umbrella_prepare_prefix_mismatch",
    )
    return record


def seal_epoch(
    state: Any,
    prepared: Any,
    *,
    now_ns: int,
    terminal_sha256: str,
    independent_replay_sha256: str,
    quiescence_sha256: str,
    all_requests_settled: bool,
    independently_replayed: bool,
    transport_quiescent: bool,
    pins_and_accounting_verified: bool,
) -> dict[str, Any]:
    """Seal a terminal epoch only after complete scan denominator and independent replay."""
    current = _verify_state(state)
    prep = _verify_prepare(prepared, current)
    _need(now_ns >= current["epoch_end_ns"], "umbrella_terminal_before_epoch_end")
    _need(
        current["next_scan_index"] == (current["epoch_index"] + 1) * SCANS_PER_EPOCH,
        "umbrella_terminal_scan_denominator",
    )
    _need(
        all(
            _hash(value)
            for value in (terminal_sha256, independent_replay_sha256, quiescence_sha256)
        )
        and all(
            value is True
            for value in (
                all_requests_settled,
                independently_replayed,
                transport_quiescent,
                pins_and_accounting_verified,
            )
        ),
        "umbrella_terminal_evidence_failed",
    )
    prepared_request_count = prep["prepared_request_count"]
    prepared_scan_count = prep["prepared_scan_count"]
    _need(
        current["accounting"]["request_count"] >= prepared_request_count
        and current["accounting"]["raw_bytes_upper_bound"]
        >= prep["prepared_accounting"]["raw_bytes_upper_bound"]
        and current["accounting"]["physical_bytes_upper_bound"]
        >= prep["prepared_accounting"]["physical_bytes_upper_bound"]
        and sha256(canonical(current["requests"][:prepared_request_count]))
        == prep["prepared_request_prefix_sha256"]
        and sha256(canonical(current["scan_records"][:prepared_scan_count]))
        == prep["prepared_scan_prefix_sha256"],
        "umbrella_terminal_prefix_changed",
    )
    core = {
        "schema": SEAL_SCHEMA,
        "schema_version": VERSION,
        "umbrella_id": current["umbrella_id"],
        "epoch_index": current["epoch_index"],
        "lease_id": current["lease_id"],
        "root_id": current["root_id"],
        "epoch_start_ns": current["epoch_start_ns"],
        "epoch_end_ns": current["epoch_end_ns"],
        "prepared_sha256": prep["prepare_sha256"],
        "terminal_sha256": terminal_sha256,
        "independent_replay_sha256": independent_replay_sha256,
        "quiescence_sha256": quiescence_sha256,
        "sealed_at_ns": now_ns,
        "release_sha256": current["release_sha256"],
        "source_sha256": current["source_sha256"],
        "policy_sha256": current["policy_sha256"],
        "state_sha256": current["state_sha256"],
        "accounting": current["accounting"],
        "request_prefix_sha256": sha256(canonical(current["requests"])),
        "scan_prefix_sha256": sha256(canonical(current["scan_records"])),
        "dispatch_allowed": False,
    }
    return {**core, "seal_sha256": sha256(canonical(core))}


def verify_seal(seal: Any, prepared: Any, state: Any) -> None:
    current = _verify_state(state)
    prep = _verify_prepare(prepared, current)
    record = _exact_map(
        seal,
        {
            "schema",
            "schema_version",
            "umbrella_id",
            "epoch_index",
            "lease_id",
            "root_id",
            "epoch_start_ns",
            "epoch_end_ns",
            "prepared_sha256",
            "terminal_sha256",
            "independent_replay_sha256",
            "quiescence_sha256",
            "sealed_at_ns",
            "release_sha256",
            "source_sha256",
            "policy_sha256",
            "state_sha256",
            "accounting",
            "request_prefix_sha256",
            "scan_prefix_sha256",
            "dispatch_allowed",
            "seal_sha256",
        },
        "umbrella_seal_shape",
    )
    core = dict(record)
    digest = core.pop("seal_sha256")
    _need(_hash(digest) and sha256(canonical(core)) == digest, "umbrella_seal_hash")
    _need(
        record.get("schema") == SEAL_SCHEMA
        and record.get("schema_version") == VERSION
        and record.get("umbrella_id") == current["umbrella_id"]
        and record.get("epoch_index") == current["epoch_index"]
        and record.get("lease_id") == current["lease_id"]
        and record.get("root_id") == current["root_id"]
        and record.get("prepared_sha256") == prep["prepare_sha256"]
        and record.get("state_sha256") == current["state_sha256"]
        and record.get("accounting") == current["accounting"]
        and record.get("request_prefix_sha256") == sha256(canonical(current["requests"]))
        and record.get("scan_prefix_sha256") == sha256(canonical(current["scan_records"]))
        and record.get("release_sha256") == current["release_sha256"]
        and record.get("source_sha256") == current["source_sha256"]
        and record.get("policy_sha256") == current["policy_sha256"]
        and record.get("dispatch_allowed") is False,
        "umbrella_seal_binding",
    )


def transfer_epoch(state: Any, prepared: Any, seal: Any) -> dict[str, Any]:
    """Create the next epoch state, carrying counters, gaps, rate history, and debt verbatim."""
    current = _verify_state(state)
    prep = _verify_prepare(prepared, current)
    verify_seal(seal, prep, current)
    next_index = current["epoch_index"] + 1
    next_start = current["epoch_end_ns"]
    history = list(current["epoch_history"])
    history.append(
        {
            "epoch_index": next_index,
            "lease_id": prep["successor_lease_id"],
            "root_id": prep["successor_root_id"],
            "epoch_start_ns": next_start,
            "epoch_end_ns": next_start + EPOCH_NS,
        }
    )
    core = _state_core(current)
    core.update(
        {
            "epoch_index": next_index,
            "epoch_start_ns": next_start,
            "epoch_end_ns": next_start + EPOCH_NS,
            "lease_id": prep["successor_lease_id"],
            "root_id": prep["successor_root_id"],
            "epoch_history": history,
            "phase": "transferred",
            "next_scan_index": next_index * SCANS_PER_EPOCH,
            "prior_seal_sha256": seal["seal_sha256"],
        }
    )
    return _seal_state(core)


def authorize_dispatch(
    state: Any,
    *,
    now_ns: int,
    release_sha256: str,
    source_sha256: str,
    policy_sha256: str,
    source_verified: bool,
    storage_verified: bool,
    identity_verified: bool,
    accounting_verified: bool,
    quiescence_verified: bool,
    prior_handoff_replayed: bool,
) -> dict[str, Any]:
    """Fail-closed gate immediately before any provider request in the current epoch."""
    current = _verify_state(state)
    _need(_positive(now_ns), "umbrella_dispatch_now")
    checks = {
        "source": source_verified is True and source_sha256 == current["source_sha256"],
        "storage": storage_verified is True,
        "identity": identity_verified is True,
        "accounting": accounting_verified is True,
        "quiescence": quiescence_verified is True,
        "release": release_sha256 == current["release_sha256"],
        "policy": policy_sha256 == current["policy_sha256"],
        "handoff": current["epoch_index"] == 0
        or (
            current["phase"] in {"transferred", "active"}
            and _hash(current.get("prior_seal_sha256"))
            and prior_handoff_replayed is True
        ),
        "stop_latch": not current["stop_latched"],
        "lease_time": now_ns < current["end_ns"],
        "epoch_time": current["epoch_start_ns"] <= now_ns < current["epoch_end_ns"],
        "scan_capacity": current["next_scan_index"]
        < (current["epoch_index"] + 1) * SCANS_PER_EPOCH,
        "cooldown": current["cooldown_until_ns"] is None or now_ns >= current["cooldown_until_ns"],
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schema": DECISION_SCHEMA,
        "allowed": not failures,
        "reason": "ready" if not failures else "fail_closed:" + ",".join(failures),
        "epoch_index": current["epoch_index"],
        "lease_id": current["lease_id"],
        "root_id": current["root_id"],
        "scan_index": current["next_scan_index"],
        "request_count": current["accounting"]["request_count"],
        "raw_bytes_upper_bound": current["accounting"]["raw_bytes_upper_bound"],
        "physical_bytes_upper_bound": current["accounting"]["physical_bytes_upper_bound"],
        "checks": checks,
        "provider_io": False,
    }


def alerts(umbrella: Mapping[str, Any], now_ns: int) -> list[str]:
    """Return cumulative operator alert thresholds; no calendar expiry is implied."""
    _need(type(umbrella) is dict and _positive(now_ns), "umbrella_alert_input")
    _need(
        _positive(umbrella.get("start_ns")) and _positive(umbrella.get("end_ns")),
        "umbrella_alert_binding",
    )
    elapsed = max(0, now_ns - umbrella["start_ns"])
    due = []
    if elapsed >= 30 * DAY_NS:
        due.append("day30_usage_review")
    if elapsed >= 40 * DAY_NS:
        due.append("day40_remaining_budget_review")
    if now_ns >= umbrella["end_ns"]:
        due.append("day45_dispatch_closed")
    return due
