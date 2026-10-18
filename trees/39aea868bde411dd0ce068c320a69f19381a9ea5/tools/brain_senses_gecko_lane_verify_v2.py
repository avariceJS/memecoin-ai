"""Independent offline verifier for Gecko lane intents, captures and bucket skips."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from brain_senses_gecko_lane_engine_v2 import (
    STATUS_CLIENT_ERROR,
    STATUS_EMPTY,
    STATUS_MALFORMED,
    STATUS_NOT_DISPATCHED,
    STATUS_NOT_DISPATCHED_SPACING,
    STATUS_NOT_DISPATCHED_STATE,
    STATUS_OVERSIZE,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_RECEIVED,
    STATUS_TRANSPORT,
    STATUS_UNSUPPORTED,
    classify_dispatch,
    cooldown_until_ns,
    dispatch_window,
    parse_response,
    request_for_bucket,
)
from brain_senses_gecko_lane_policy_v2 import (
    MAX_INTENTS_PER_ROLLING_30D,
    MAX_RAW_BYTES_PER_ROLLING_30D,
    NS,
    canonical,
    sha256,
    verify_policy,
)

TERMINAL_STATUSES = {
    STATUS_RECEIVED,
    STATUS_EMPTY,
    STATUS_UNSUPPORTED,
    STATUS_RATE_LIMITED,
    STATUS_CLIENT_ERROR,
    STATUS_PROVIDER_ERROR,
    STATUS_TRANSPORT,
    STATUS_MALFORMED,
    STATUS_OVERSIZE,
    STATUS_NOT_DISPATCHED,
    STATUS_NOT_DISPATCHED_SPACING,
    STATUS_NOT_DISPATCHED_STATE,
}


def _check_rolling_30d_limits(events: Sequence[tuple[int, int, int]]) -> None:
    """Check every inclusive 30-day window; rows are (time_ns, intents, raw_bytes)."""
    window_ns = 30 * 86400 * NS
    ordered = sorted(events, key=lambda row: row[0])
    left = 0
    intents = 0
    raw_bytes = 0
    for row in ordered:
        at_ns, intent_delta, raw_delta = row
        if (
            type(at_ns) is not int
            or at_ns < 0
            or type(intent_delta) is not int
            or intent_delta < 0
            or type(raw_delta) is not int
            or raw_delta < 0
        ):
            raise ValueError("gecko_rolling_usage_invalid")
        intents += intent_delta
        raw_bytes += raw_delta
        while at_ns - ordered[left][0] > window_ns:
            intents -= ordered[left][1]
            raw_bytes -= ordered[left][2]
            left += 1
        if intents > MAX_INTENTS_PER_ROLLING_30D:
            raise ValueError("gecko_rolling_30d_intent_cap")
        if raw_bytes > MAX_RAW_BYTES_PER_ROLLING_30D:
            raise ValueError("gecko_rolling_30d_raw_cap")


def verify_journal(
    policy: dict[str, Any],
    intents: Sequence[dict[str, Any]],
    terminals: Sequence[dict[str, Any]],
    raw_responses: Mapping[str, bytes],
    skips: Sequence[dict[str, Any]] = (),
    scheduled_closes_ns: Sequence[int] | None = None,
) -> dict[str, Any]:
    verify_policy(policy)
    intent_by_id: dict[str, dict[str, Any]] = {}
    pool_bucket: set[tuple[str, int]] = set()
    intent_dates: dict[str, int] = {}
    rolling_usage_events: list[tuple[int, int, int]] = []
    pool_ids = {row["pool_id"] for row in policy["pools"]}
    for intent in intents:
        if not isinstance(intent, dict) or set(intent) != {
            "schema",
            "request_id",
            "request",
            "decision_ns",
            "provider_charge_units",
            "provider_charge_status",
            "intent_sha256",
        }:
            raise ValueError("gecko_intent_shape")
        if (
            intent["schema"] != "brain-senses-gecko-intent-v2"
            or intent["provider_charge_units"] is not None
            or intent["provider_charge_status"] != "unknown_unverified"
        ):
            raise ValueError("gecko_intent_semantics")
        request = intent["request"]
        if not isinstance(request, dict):
            raise ValueError("gecko_intent_request")
        request_pool_id = request.get("pool_id")
        request_close_ns = request.get("bucket_close_ns")
        if (
            not isinstance(request_pool_id, str)
            or type(request_close_ns) is not int
            or request_pool_id not in pool_ids
            or request != request_for_bucket(policy, request_pool_id, request_close_ns)
        ):
            raise ValueError("gecko_intent_request")
        decision = intent["decision_ns"]
        if (
            type(decision) is not int
            or classify_dispatch(request["bucket_close_ns"], decision) != "due"
        ):
            raise ValueError("gecko_intent_decision_window")
        core = dict(intent)
        claimed = core.pop("intent_sha256")
        if claimed != sha256(canonical(core)):
            raise ValueError("gecko_intent_hash")
        key = intent["request_id"]
        identity = (request["pool_id"], request["bucket_close_ns"])
        if key in intent_by_id or identity in pool_bucket:
            raise ValueError("gecko_intent_duplicate")
        intent_by_id[key] = intent
        pool_bucket.add(identity)
        rolling_usage_events.append((decision, 1, 0))
        intent_day = str(decision // (86400 * NS))
        intent_dates[intent_day] = intent_dates.get(intent_day, 0) + 1
        if intent_dates[intent_day] > policy["protocol"]["maximum_intents_per_utc_day"]:
            raise ValueError("gecko_daily_intent_cap")

    if len(terminals) > len(intents):
        raise ValueError("gecko_orphan_terminal")
    terminal_by_id: dict[str, dict[str, Any]] = {}
    last_dispatch_ns: int | None = None
    last_decision_ns: int | None = None
    cooldown: tuple[int | None, str | None] = (None, None)
    fatal_stop = False
    lifetime_bytes = 0
    stored_bytes = 0
    observed_response_bytes = 0
    observed_planned_bars = 0
    daily_bytes: dict[str, int] = {}
    for terminal in sorted(terminals, key=lambda row: row.get("request_started_ns", -1)):
        request_id = terminal.get("request_id")
        if not isinstance(request_id, str):
            raise ValueError("gecko_terminal_request_id")
        terminal_intent = intent_by_id.get(request_id)
        if terminal_intent is None or request_id in terminal_by_id:
            raise ValueError("gecko_terminal_binding")
        if (
            terminal.get("schema") != "brain-senses-gecko-terminal-v2"
            or terminal.get("response_schema") != "brain-senses-gecko-response-v2"
            or terminal.get("intent_sha256") != terminal_intent["intent_sha256"]
        ):
            raise ValueError("gecko_terminal_schema_binding")
        status = terminal.get("status")
        if status not in TERMINAL_STATUSES:
            raise ValueError("gecko_terminal_status")
        started = terminal.get("request_started_ns")
        receipt = terminal.get("receipt_ns")
        if type(started) is not int or type(receipt) is not int or receipt < started:
            raise ValueError("gecko_terminal_time")
        request = terminal_intent["request"]
        start_window, end_window = dispatch_window(request["bucket_close_ns"])
        if status in {
            STATUS_NOT_DISPATCHED,
            STATUS_NOT_DISPATCHED_SPACING,
            STATUS_NOT_DISPATCHED_STATE,
        }:
            if terminal.get("http_status") is not None:
                raise ValueError("gecko_not_dispatched_shape")
            if status == STATUS_NOT_DISPATCHED and started < end_window:
                raise ValueError("gecko_not_dispatched_deadline")
            if status == STATUS_NOT_DISPATCHED_SPACING and (
                not start_window <= started < end_window
                or last_dispatch_ns is None
                or started - last_dispatch_ns >= 2 * NS
            ):
                raise ValueError("gecko_not_dispatched_spacing")
            if status == STATUS_NOT_DISPATCHED_STATE and not start_window <= started < end_window:
                raise ValueError("gecko_not_dispatched_state_window")
        else:
            if not start_window <= started < end_window:
                raise ValueError("gecko_request_started_window")
            if started < terminal_intent["decision_ns"]:
                raise ValueError("gecko_intent_after_dispatch")
            if last_dispatch_ns is not None and started - last_dispatch_ns < 2 * NS:
                raise ValueError("gecko_request_spacing")
            if last_dispatch_ns is not None and started < last_dispatch_ns:
                raise ValueError("gecko_dispatch_order")
            if last_decision_ns is not None and terminal_intent["decision_ns"] < last_decision_ns:
                raise ValueError("gecko_intent_order")
            if (
                cooldown[1] == "contract_access_review_required"
                or cooldown[1] == "review_required_rate_limit"
            ):
                raise ValueError("gecko_dispatch_after_review_stop")
            if cooldown[0] is not None and started < cooldown[0]:
                raise ValueError("gecko_dispatch_during_cooldown")
            if fatal_stop:
                raise ValueError("gecko_dispatch_after_terminal_stop")
            last_dispatch_ns = started
            last_decision_ns = terminal_intent["decision_ns"]

        body = raw_responses.get(request_id)
        body_sha = terminal.get("response_sha256")
        expected_capture_state = (
            "raw_response_durable_candidate"
            if body is not None
            else "response_over_budget_unretained"
            if status == STATUS_OVERSIZE
            else "no_response"
        )
        if terminal.get("response_capture_state") != expected_capture_state:
            raise ValueError("gecko_response_capture_state")
        if body is None:
            if status == STATUS_OVERSIZE:
                if body_sha is not None or terminal.get("response_bytes", 0) <= 0:
                    raise ValueError("gecko_oversize_unpersisted")
            elif body_sha is not None or terminal.get("response_bytes") != 0:
                raise ValueError("gecko_terminal_missing_raw")
        else:
            if (
                not isinstance(body, bytes)
                or body_sha != sha256(body)
                or terminal.get("response_bytes") != len(body)
            ):
                raise ValueError("gecko_raw_binding")
            stored_bytes += len(body)
        response_length = terminal.get("response_bytes")
        if type(response_length) is not int or response_length < 0:
            raise ValueError("gecko_response_length")
        if response_length:
            lifetime_bytes += response_length
            observed_response_bytes += response_length
            response_day = str(started // (86400 * NS))
            daily_bytes[response_day] = daily_bytes.get(response_day, 0) + response_length
            if daily_bytes[response_day] > policy["protocol"]["maximum_raw_bytes_per_utc_day"]:
                raise ValueError("gecko_raw_byte_cap")
            rolling_usage_events.append((started, 0, response_length))
        if terminal.get("availability_ns") != (receipt if body is not None else None):
            raise ValueError("gecko_availability")
        if status in {STATUS_RECEIVED, STATUS_EMPTY}:
            if body is None or terminal.get("http_status") != 200:
                raise ValueError("gecko_success_without_raw")
            bars = parse_response(body, request["bucket_close_ns"], receipt)
            expected_status = STATUS_RECEIVED if bars else STATUS_EMPTY
            if expected_status != status or terminal.get("bars") != bars:
                raise ValueError("gecko_parsed_response_mismatch")
            observed_planned_bars += sum(
                row["disposition"] in {"eligible_new_open", "eligible_overlap_revision"}
                for row in bars
            )
            if terminal.get("availability_ns") != receipt:
                raise ValueError("gecko_availability")
        elif status == STATUS_MALFORMED:
            if body is None or terminal.get("http_status") != 200 or terminal.get("bars") != []:
                raise ValueError("gecko_malformed_capture")
            try:
                parse_response(body, request["bucket_close_ns"], receipt)
            except ValueError:
                pass
            else:
                raise ValueError("gecko_malformed_claim")
        elif status == STATUS_OVERSIZE:
            if (
                body is not None
                or terminal.get("response_sha256") is not None
                or terminal.get("availability_ns") is not None
                or terminal.get("http_status") != 200
                or terminal.get("response_bytes", 0) <= request["max_response_bytes"]
                or terminal.get("bars") != []
            ):
                raise ValueError("gecko_oversize_capture")
            fatal_stop = True
        elif status == STATUS_UNSUPPORTED:
            if terminal.get("http_status") not in {404, 410, 422}:
                raise ValueError("gecko_unsupported_status")
        elif status == STATUS_RATE_LIMITED and terminal.get("http_status") != 429:
            raise ValueError("gecko_rate_limit_status")
        elif status == STATUS_CLIENT_ERROR and not 400 <= terminal.get("http_status", 0) < 500:
            raise ValueError("gecko_client_status")
        elif status == STATUS_PROVIDER_ERROR and not (
            terminal.get("http_status", 0) >= 500
            or terminal.get("http_status", 0) < 300
            or 300 <= terminal.get("http_status", 0) < 400
        ):
            raise ValueError("gecko_provider_status")
        elif status == STATUS_TRANSPORT and terminal.get("http_status") is not None:
            raise ValueError("gecko_transport_status")
        if (
            terminal.get("provider_charge_units") is not None
            or terminal.get("provider_charge_status") != "unknown_unverified"
        ):
            raise ValueError("gecko_charge_claim")
        expected_cooldown = cooldown_until_ns(status, receipt, terminal.get("retry_after_seconds"))
        if (
            terminal.get("cooldown_until_ns"),
            terminal.get("cooldown_reason"),
        ) != expected_cooldown:
            raise ValueError("gecko_cooldown_binding")
        if status in {
            STATUS_CLIENT_ERROR,
            STATUS_RATE_LIMITED,
            STATUS_PROVIDER_ERROR,
            STATUS_TRANSPORT,
        }:
            if (
                status == STATUS_CLIENT_ERROR
                or expected_cooldown[1] == "review_required_rate_limit"
            ):
                cooldown = (
                    None,
                    "contract_access_review_required"
                    if status == STATUS_CLIENT_ERROR
                    else expected_cooldown[1],
                )
            elif expected_cooldown[0] is not None:
                cooldown = expected_cooldown
        terminal_by_id[request_id] = terminal

    # A missing terminal is an unresolved, spent intent. No later intent may follow it.
    ordered_intents = sorted(intents, key=lambda row: row["decision_ns"])
    for index, intent in enumerate(ordered_intents):
        if intent["request_id"] not in terminal_by_id and index != len(ordered_intents) - 1:
            raise ValueError("gecko_intent_after_unresolved")

    skip_by_identity: set[tuple[str, int]] = set()
    for skip in skips:
        if not isinstance(skip, dict) or set(skip) != {
            "schema",
            "pool_id",
            "bucket_close_ns",
            "at_ns",
            "reason",
            "cooldown_until_ns",
        }:
            raise ValueError("gecko_skip_shape")
        if skip["schema"] != "brain-senses-gecko-skip-v2" or skip["pool_id"] not in pool_ids:
            raise ValueError("gecko_skip_identity")
        identity = (skip["pool_id"], skip["bucket_close_ns"])
        if identity in pool_bucket or identity in skip_by_identity:
            raise ValueError("gecko_skip_duplicate_or_spent")
        skip_by_identity.add(identity)
        start, end = dispatch_window(skip["bucket_close_ns"])
        at = skip["at_ns"]
        if skip["reason"] == "missed_dispatch":
            if type(at) is not int or at < end or skip["cooldown_until_ns"] is not None:
                raise ValueError("gecko_missed_skip")
        elif skip["reason"] in {
            "rate_limit_cooldown",
            "provider_or_transport_cooldown",
            "contract_access_review_required",
        }:
            if type(at) is not int or not start <= at < end:
                raise ValueError("gecko_cooldown_skip_window")
            if skip["reason"] == "contract_access_review_required":
                valid_pause = any(
                    row["status"] == STATUS_CLIENT_ERROR
                    and row["receipt_ns"] <= at
                    and skip["cooldown_until_ns"] is None
                    for row in terminals
                )
            else:
                valid_pause = any(
                    row["cooldown_reason"] == skip["reason"]
                    and row["receipt_ns"] <= at
                    and (row["cooldown_until_ns"] is None or row["cooldown_until_ns"] > at)
                    and row["cooldown_until_ns"] == skip["cooldown_until_ns"]
                    for row in terminals
                )
            if not valid_pause:
                raise ValueError("gecko_skip_without_cooldown")
        else:
            raise ValueError("gecko_skip_reason")

    if scheduled_closes_ns is not None:
        expected_identities = {
            (pool_id, close) for close in scheduled_closes_ns for pool_id in pool_ids
        }
        if any(
            type(close) is not int or close <= 0 or close % (900 * NS)
            for close in scheduled_closes_ns
        ):
            raise ValueError("gecko_schedule_alignment")
        observed = pool_bucket | skip_by_identity
        if observed != expected_identities:
            raise ValueError("gecko_schedule_denominator")

    _check_rolling_30d_limits(rolling_usage_events)

    return {
        "status": "verified_prefix",
        "frozen_pool_count": len(pool_ids),
        "intents": len(intents),
        "terminal_count": len(terminals),
        "unresolved_spent_intents": len(intents) - len(terminals),
        "skipped_pool_buckets": len(skips),
        "planned_bar_slots": 6 * (len(intents) + len(skips)),
        "observed_planned_bar_rows": observed_planned_bars,
        "missing_planned_bar_slots_unknown": max(
            0, 6 * (len(intents) + len(skips)) - observed_planned_bars
        ),
        "missing_is_zero_activity_claim": False,
        "coverage_complete": False,
        "raw_bytes": lifetime_bytes,
        "stored_raw_bytes": stored_bytes,
        "maximum_raw_bytes_per_rolling_30d": MAX_RAW_BYTES_PER_ROLLING_30D,
        "maximum_intents_per_rolling_30d": MAX_INTENTS_PER_ROLLING_30D,
        "observed_response_bytes": observed_response_bytes,
        "daily_intents": intent_dates,
        "daily_raw_bytes": daily_bytes,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
        "activation_authorized": False,
        "cooldown_until_ns": cooldown[0],
        "pause_reason": cooldown[1],
    }
