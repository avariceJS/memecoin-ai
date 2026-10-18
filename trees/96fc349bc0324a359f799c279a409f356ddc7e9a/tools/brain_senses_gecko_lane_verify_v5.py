"""Independent verifier for Gecko schedule-v4 intents and terminal captures.

This module recomputes all schedule boundaries locally. It deliberately does not
import or call v3 dispatch-window/classification helpers.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from brain_senses_external_clock_v1 import ClockError, validate_progress
from brain_senses_gecko_lane_engine_v3 import parse_response
from brain_senses_gecko_lane_policy_v3 import NS, canonical, sha256
from brain_senses_gecko_lane_policy_v4 import verify_policy_v4

_INTENT_SCHEMA = "brain-senses-gecko-intent-v4"
INTENT_SCHEMA_V4 = _INTENT_SCHEMA
INTENT_SCHEMA_V4 = _INTENT_SCHEMA
_REQUEST_SCHEMA = "brain-senses-gecko-request-v4"
_TERMINAL_SCHEMA = "brain-senses-gecko-terminal-v4"
_MIN_SPACING_NS = 12 * NS
_WINDOW_START_NS = 60 * NS
_LATEST_START_NS = 215 * NS
_WINDOW_END_NS = 240 * NS
_CLOCK_NAME = re.compile(r"clocks/([0-9]+)-([0-9]{2})\.json\Z")
_CLOCK_INTENT_FIELDS = {"clock_path", "clock_sha256", "decision_monotonic_ns"}


def _schedule_close(value: Any) -> bool:
    return type(value) is int and value > 0 and value % (900 * NS) == 0


def _expected_request(policy: dict[str, Any], pool_id: str, close_ns: int) -> dict[str, Any]:
    """Verifier-owned reconstruction of the exact frozen GET request."""
    if not _schedule_close(close_ns):
        raise ValueError("gecko_v5_schedule_alignment")
    pool = next((entry for entry in policy["pools"] if entry["pool_id"] == pool_id), None)
    if pool is None:
        raise ValueError("gecko_v5_unknown_pool")
    close_s = close_ns // NS
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
    headers = dict(policy["protocol"]["headers"])
    semantic = {"method": "GET", "url": url, "headers": headers}
    request_id = hashlib.sha256(
        canonical(
            {
                "policy_sha256": policy["policy_sha256"],
                "pool_id": pool_id,
                "bucket_close_ns": close_ns,
            }
        )
    ).hexdigest()
    return {
        "schema": _REQUEST_SCHEMA,
        "request_id": request_id,
        "pool_id": pool_id,
        "base_mint": pool["base_mint"],
        "quote_mint": pool["quote_mint"],
        "bucket_close_ns": close_ns,
        "bucket_id": str(close_s),
        "path": path,
        "query": query,
        "url": url,
        "headers": headers,
        "semantic_request_sha256": hashlib.sha256(canonical(semantic)).hexdigest(),
        "max_response_bytes": min(
            policy["protocol"]["maximum_raw_response_bytes"],
            policy["protocol"]["peak_storage_bytes"],
            policy["protocol"]["maximum_raw_bytes_per_utc_day"],
            policy["protocol"]["maximum_raw_bytes_per_rolling_30d"],
        ),
    }


def _within_close(value_ns: Any, close_ns: int, offset: int) -> bool:
    return type(value_ns) is int and close_ns + offset * NS <= value_ns


def verify_journal_v5(
    policy: dict[str, Any],
    intents: Sequence[dict[str, Any]],
    terminals: Sequence[dict[str, Any]],
    raw_responses: Mapping[str, bytes],
    dispatch_records: Sequence[dict[str, Any]] = (),
    *,
    root: str | None = None,
    clock_files: Mapping[str, bytes] | None = None,
    skips: Sequence[dict[str, Any]] = (),
    scheduled_closes_ns: Sequence[int] | None = None,
    confirmations: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Verify schedule and integrity; incomplete and late results stay non-authorizing."""
    verify_policy_v4(policy)
    pool_ids = [row["pool_id"] for row in policy["pools"]]
    if (root is None) != (clock_files is None):
        raise ValueError("gecko_v5_clock_arguments")
    if root is not None and (
        not isinstance(root, str) or not root or root.startswith("/") or ".." in root.split("/")
    ):
        raise ValueError("gecko_v5_root")
    clock_files = {} if clock_files is None else clock_files
    expected_fields = {
        "schema",
        "request_id",
        "request",
        "decision_ns",
        "provider_charge_units",
        "provider_charge_status",
        "intent_sha256",
    }
    intents_by_id: dict[str, dict[str, Any]] = {}
    pool_bucket: set[tuple[str, int]] = set()
    referenced_clocks: set[str] = set()
    for intent in intents:
        has_clock = isinstance(intent, dict) and bool(_CLOCK_INTENT_FIELDS & set(intent))
        fields = expected_fields | _CLOCK_INTENT_FIELDS if has_clock else expected_fields
        if not isinstance(intent, dict) or set(intent) != fields:
            raise ValueError("gecko_v5_intent_shape")
        if (
            intent["schema"] != _INTENT_SCHEMA
            or intent["provider_charge_units"] is not None
            or intent["provider_charge_status"] != "unknown_unverified"
        ):
            raise ValueError("gecko_v5_intent_semantics")
        request = intent["request"]
        if not isinstance(request, dict):
            raise ValueError("gecko_v5_request_shape")
        pool_id, close_ns = request.get("pool_id"), request.get("bucket_close_ns")
        if (
            not isinstance(pool_id, str)
            or pool_id not in pool_ids
            or type(close_ns) is not int
            or not _schedule_close(close_ns)
        ):
            raise ValueError("gecko_v5_request_identity")
        if request != _expected_request(policy, pool_id, close_ns):
            raise ValueError("gecko_v5_request_mismatch")
        decision_ns = intent["decision_ns"]
        # A durable intent can be written only while a request could still start.
        if (
            type(decision_ns) is not int
            or decision_ns < close_ns + _WINDOW_START_NS
            or decision_ns >= close_ns + _LATEST_START_NS
        ):
            raise ValueError("gecko_v5_intent_window")
        if root is not None:
            if not has_clock:
                raise ValueError("gecko_v5_clock_missing")
            clock_path = intent["clock_path"]
            suffix = (
                clock_path[len(root) + 1 :]
                if isinstance(clock_path, str) and clock_path.startswith(root + "/")
                else ""
            )
            match = _CLOCK_NAME.fullmatch(suffix)
            if match is None or int(match.group(1)) != close_ns:
                raise ValueError("gecko_v5_clock_path")
            clock_raw = clock_files.get(clock_path)
            if not isinstance(clock_raw, bytes) or sha256(clock_raw) != intent["clock_sha256"]:
                raise ValueError("gecko_v5_clock_hash")
            try:
                record = json.loads(clock_raw)
                if canonical(record) != clock_raw:
                    raise ValueError("gecko_v5_clock_noncanonical")
                validate_progress(
                    record,
                    wall_ns=decision_ns,
                    monotonic_ns=intent["decision_monotonic_ns"],
                )
            except (ClockError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("gecko_v5_clock_progress") from exc
            referenced_clocks.add(clock_path)
        core = dict(intent)
        digest = core.pop("intent_sha256")
        if digest != sha256(canonical(core)):
            raise ValueError("gecko_v5_intent_hash")
        identity = (pool_id, close_ns)
        if intent["request_id"] in intents_by_id or identity in pool_bucket:
            raise ValueError("gecko_v5_duplicate_intent")
        intents_by_id[intent["request_id"]] = intent
        pool_bucket.add(identity)
    if root is not None and set(clock_files) != referenced_clocks:
        raise ValueError("gecko_v5_clock_closure")

    dispatch_by_request: dict[str, dict[str, Any]] = {}
    previous_marker_ns: int | None = None
    previous_hash: str | None = None
    for ordinal, marker in enumerate(dispatch_records):
        if not isinstance(marker, dict) or set(marker) != {
            "schema",
            "sequence",
            "request_id",
            "dispatch_ns",
            "previous_sha256",
            "record_sha256",
        }:
            raise ValueError("gecko_v5_dispatch_shape")
        core = dict(marker)
        digest = core.pop("record_sha256")
        at = marker["dispatch_ns"]
        if (
            marker["schema"] != "brain-senses-gecko-dispatch-v3"
            or marker["sequence"] != ordinal
            or marker["previous_sha256"] != previous_hash
            or digest != sha256(canonical(core))
            or type(at) is not int
            or (previous_marker_ns is not None and at - previous_marker_ns < _MIN_SPACING_NS)
            or marker["request_id"] not in intents_by_id
            or marker["request_id"] in dispatch_by_request
        ):
            raise ValueError("gecko_v5_dispatch_chain")
        intent = intents_by_id[marker["request_id"]]
        close_ns = intent["request"]["bucket_close_ns"]
        if (
            at < close_ns + _WINDOW_START_NS
            or at >= close_ns + _LATEST_START_NS
            or at < intent["decision_ns"]
        ):
            raise ValueError("gecko_v5_dispatch_window")
        dispatch_by_request[marker["request_id"]] = marker
        previous_marker_ns, previous_hash = at, digest

    times = [row["dispatch_ns"] for row in dispatch_records]
    left = 0
    for right, at in enumerate(times):
        while at - times[left] >= 60 * NS:
            left += 1
        if right - left + 1 > policy["protocol"]["maximum_dispatches_per_rolling_60_seconds"]:
            raise ValueError("gecko_v5_rolling_dispatch_limit")

    if len(terminals) > len(intents):
        raise ValueError("gecko_v5_orphan_terminal")
    terminal_by_id: dict[str, dict[str, Any]] = {}
    charged_raw = 0
    late_unknown = 0
    timely_available = 0
    eligible_new_open = 0
    for terminal in terminals:
        if not isinstance(terminal, dict):
            raise ValueError("gecko_v5_terminal_shape")
        request_id_raw = terminal.get("request_id")
        if not isinstance(request_id_raw, str):
            raise ValueError("gecko_v5_terminal_request_id")
        request_id = request_id_raw
        found_intent = intents_by_id.get(request_id)
        if found_intent is None or request_id in terminal_by_id:
            raise ValueError("gecko_v5_terminal_binding")
        if (
            terminal.get("schema") != _TERMINAL_SCHEMA
            or terminal.get("intent_sha256") != found_intent["intent_sha256"]
            or terminal.get("provider_charge_units") is not None
            or terminal.get("provider_charge_status") != "unknown_unverified"
        ):
            raise ValueError("gecko_v5_terminal_semantics")
        close_ns = found_intent["request"]["bucket_close_ns"]
        dispatch_ns = terminal.get("dispatch_ns")
        started_ns = terminal.get("request_started_ns")
        receipt_ns = terminal.get("receipt_ns")
        availability_ns = terminal.get("availability_ns")
        status = terminal.get("status")
        found_marker = dispatch_by_request.get(request_id)
        no_dispatch_statuses = {
            "intent_not_dispatched_window_expired",
            "intent_not_dispatched_spacing",
            "intent_not_dispatched_state_changed",
            "intent_not_dispatched_deadline_margin",
        }
        if found_marker is None:
            if (
                status not in no_dispatch_statuses
                or terminal.get("dispatch_record_sha256") is not None
                or type(dispatch_ns) is not int
                or dispatch_ns < found_intent["decision_ns"]
                or type(started_ns) is not int
                or started_ns < dispatch_ns
                or type(receipt_ns) is not int
                or receipt_ns < started_ns
                or terminal.get("response_sha256") is not None
            ):
                raise ValueError("gecko_v5_terminal_times")
        elif (
            terminal.get("dispatch_record_sha256") != found_marker["record_sha256"]
            or dispatch_ns != found_marker["dispatch_ns"]
            or type(started_ns) is not int
            or started_ns < dispatch_ns
            or started_ns >= close_ns + _LATEST_START_NS
            or type(receipt_ns) is not int
            or receipt_ns < started_ns
        ):
            raise ValueError("gecko_v5_terminal_times")
        body = raw_responses.get(request_id)
        if body is None:
            if availability_ns is not None:
                raise ValueError("gecko_v5_missing_raw_availability")
            if status == "response_over_budget":
                response_bytes = terminal.get("response_bytes")
                maximum = intent["request"]["max_response_bytes"]
                if (
                    terminal.get("response_sha256") is not None
                    or type(response_bytes) is not int
                    or not maximum < response_bytes <= maximum + 1
                ):
                    raise ValueError("gecko_v5_oversize_capture")
                charged_raw += response_bytes
            elif terminal.get("response_sha256") is not None or terminal.get("response_bytes") != 0:
                raise ValueError("gecko_v5_missing_raw")
        else:
            if (
                not isinstance(body, bytes)
                or terminal.get("response_sha256") != sha256(body)
                or terminal.get("response_bytes") != len(body)
            ):
                raise ValueError("gecko_v5_raw_binding")
            charged_raw += len(body)
        if body is not None and availability_ns is not None:
            if type(availability_ns) is not int or availability_ns < receipt_ns:
                raise ValueError("gecko_v5_availability_order")
            if availability_ns < close_ns + _WINDOW_END_NS:
                if terminal.get("timeliness") != "available":
                    raise ValueError("gecko_v5_timeliness_claim")
                timely_available += 1
            else:
                if terminal.get("timeliness") != "late_unknown_spent_no_retry":
                    raise ValueError("gecko_v5_late_result_claim")
                late_unknown += 1
        elif terminal.get("timeliness") not in {"unresolved_spent_unknown", "no_response"}:
            raise ValueError("gecko_v5_unresolved_claim")

        if status in {"received", "empty_response"}:
            if body is None or terminal.get("http_status") != 200:
                raise ValueError("gecko_v5_success_capture")
            parsed = parse_response(body, close_ns, receipt_ns)
            persisted_bars = []
            for bar in parsed:
                projected = dict(bar)
                provider_receipt = projected.pop("source_available_ns", None)
                projected["provider_receipt_ns"] = provider_receipt
                projected["provider_receipt_semantics"] = (
                    "transport_receipt_only_not_decision_eligibility"
                )
                persisted_bars.append(projected)
            expected_status = "received" if parsed else "empty_response"
            if persisted_bars != terminal.get("bars") or status != expected_status:
                raise ValueError("gecko_v5_parsed_body_mismatch")
            if terminal.get("timeliness") == "available":
                eligible_new_open += sum(
                    row["disposition"] == "eligible_new_open" for row in parsed
                )
        terminal_by_id[request_id] = terminal

    confirmation_by_request: dict[str, dict[str, Any]] = {}
    previous_confirmation_hash: str | None = None
    for ordinal, row in enumerate(confirmations):
        if not isinstance(row, dict) or set(row) != {
            "schema",
            "sequence",
            "request_id",
            "outcome_sha256",
            "confirmed_ns",
            "previous_sha256",
            "record_sha256",
        }:
            raise ValueError("gecko_v5_confirmation_shape")
        core = dict(row)
        claimed = core.pop("record_sha256")
        request_id_raw = row["request_id"]
        if not isinstance(request_id_raw, str):
            raise ValueError("gecko_v5_confirmation_request_id")
        request_id = request_id_raw
        found_terminal = terminal_by_id.get(request_id)
        if (
            row["schema"] != "brain-senses-gecko-result-confirmation-v3"
            or row["sequence"] != ordinal
            or row["previous_sha256"] != previous_confirmation_hash
            or claimed != sha256(canonical(core))
            or request_id in confirmation_by_request
            or found_terminal is None
            or row["outcome_sha256"] != sha256(canonical(found_terminal))
            or type(row["confirmed_ns"]) is not int
            or row["confirmed_ns"] < found_terminal["receipt_ns"]
            or (
                found_terminal.get("availability_ns") is not None
                and row["confirmed_ns"] < found_terminal["availability_ns"]
            )
        ):
            raise ValueError("gecko_v5_confirmation_binding")
        confirmation_by_request[request_id] = row
        previous_confirmation_hash = claimed

    dispatched_sorted = sorted(
        dispatch_by_request.values(), key=lambda record: record["dispatch_ns"]
    )
    for prior, current in zip(dispatched_sorted, dispatched_sorted[1:], strict=False):
        prior_confirmation = confirmation_by_request.get(prior["request_id"])
        if (
            prior_confirmation is None
            or current["dispatch_ns"] - prior_confirmation["confirmed_ns"] < 20 * NS
        ):
            raise ValueError("gecko_v5_durable_result_spacing")

    skip_identities: set[tuple[str, int]] = set()
    for skip in skips:
        if not isinstance(skip, dict) or set(skip) != {
            "schema",
            "pool_id",
            "bucket_close_ns",
            "at_ns",
            "reason",
            "cooldown_until_ns",
        }:
            raise ValueError("gecko_v5_skip_shape")
        identity = (skip["pool_id"], skip["bucket_close_ns"])
        if (
            skip["schema"] != "brain-senses-gecko-skip-v4"
            or skip["pool_id"] not in pool_ids
            or not _schedule_close(skip["bucket_close_ns"])
            or identity in pool_bucket
            or identity in skip_identities
            or skip["reason"] != "missed_dispatch"
            or type(skip["at_ns"]) is not int
            or skip["at_ns"] < skip["bucket_close_ns"] + _LATEST_START_NS
            or skip["cooldown_until_ns"] is not None
        ):
            raise ValueError("gecko_v5_skip_semantics")
        skip_identities.add(identity)

    bucket_closes = sorted({close for _pool, close in pool_bucket | skip_identities})
    if scheduled_closes_ns is not None:
        if any(not _schedule_close(close) for close in scheduled_closes_ns):
            raise ValueError("gecko_v5_scheduled_close")
        if len(set(scheduled_closes_ns)) != len(scheduled_closes_ns):
            raise ValueError("gecko_v5_duplicate_scheduled_close")
        bucket_closes = sorted(set(scheduled_closes_ns) | set(bucket_closes))
        expected = {(pool_id, close) for pool_id in pool_ids for close in scheduled_closes_ns}
        if (pool_bucket | skip_identities) - expected:
            raise ValueError("gecko_v5_identity_outside_schedule")

    # A request marker is spent even when its response is absent; intent/marker may not be replayed.
    if set(dispatch_by_request) - set(intents_by_id):
        raise ValueError("gecko_v5_marker_without_intent")
    if len(dispatch_by_request) > len(intents_by_id):
        raise ValueError("gecko_v5_marker_count")
    missing = [
        {"pool_id": pool_id, "bucket_close_ns": close}
        for close in bucket_closes
        for pool_id in pool_ids
        if (pool_id, close) not in pool_bucket | skip_identities
    ]
    release_ok = (
        bool(bucket_closes)
        and not missing
        and not skip_identities
        and len(intents) == len(pool_ids) * len(bucket_closes)
        and len(dispatch_by_request) == len(intents)
        and len(terminals) == len(intents)
        and len(confirmation_by_request) == len(terminals)
        and timely_available == len(intents)
        and late_unknown == 0
        and all(row.get("status") in {"received", "empty_response"} for row in terminals)
        and eligible_new_open >= 1
    )
    return {
        "status": "verified_prefix",
        "schedule_version": 4,
        "intent_contract": _INTENT_SCHEMA,
        "intents": len(intents),
        "dispatched": len(dispatch_by_request),
        "terminal_count": len(terminals),
        "confirmed_result_count": len(confirmation_by_request),
        "unresolved_spent": len(dispatch_by_request) - len(terminals),
        "timely_available": timely_available,
        "late_unknown_spent_no_retry": late_unknown,
        "raw_bytes": charged_raw,
        "scheduled_bucket_count": len(bucket_closes),
        "missing_pool_buckets_unknown": missing,
        "eligible_new_open": eligible_new_open,
        "release_qualification_allowed": release_ok,
        "activation_authorized": False,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }


__all__ = ["INTENT_SCHEMA_V4", "verify_journal", "verify_journal_v5"]

verify_journal = verify_journal_v5
