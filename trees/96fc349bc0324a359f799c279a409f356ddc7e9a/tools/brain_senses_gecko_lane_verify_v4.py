"""Independent exact verifier for clock-bound Gecko intent-v4 journals.

Clock bytes are supplied by the caller after descriptor-relative reads from the
same immutable pilot root.  This module has no filesystem, network, or provider
defaults.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from brain_senses_external_clock_v1 import ClockError, validate_progress
from brain_senses_gecko_lane_engine_v3 import (
    STATUS_EMPTY,
    STATUS_PROVIDER_ERROR,
    STATUS_RECEIVED,
    STATUS_TRANSPORT,
    parse_response,
)
from brain_senses_gecko_lane_policy_v3 import canonical, sha256
from brain_senses_gecko_lane_verify_v3 import verify_journal as _verify_semantics

INTENT_SCHEMA_V4 = "brain-senses-gecko-intent-v4"
INTENT_SCHEMA_CLOCKED_V3 = "brain-senses-gecko-intent-v3"
_CLOCK_NAME = re.compile(r"clocks/([0-9]+)-([0-9]{2})\.json\Z")
_CLOCK_FIELDS = {"clock_path", "clock_sha256", "decision_monotonic_ns"}
_INTENT_FIELDS = {
    "schema",
    "request_id",
    "request",
    "decision_ns",
    "provider_charge_units",
    "provider_charge_status",
    "intent_sha256",
    *_CLOCK_FIELDS,
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("gecko_clock_duplicate_key")
        value[key] = item
    return value


def verify_journal(
    policy: dict[str, Any],
    intents: Sequence[dict[str, Any]],
    terminals: Sequence[dict[str, Any]],
    raw_responses: Mapping[str, bytes],
    *,
    root: str,
    clock_files: Mapping[str, bytes],
    skips: Sequence[dict[str, Any]] = (),
    scheduled_closes_ns: Sequence[int] | None = None,
    dispatch_records: Sequence[dict[str, Any]] = (),
    allow_clocked_v3_forensics: bool = False,
) -> dict[str, Any]:
    """Verify exact intent bytes, clock closure, and shared v3 journal semantics.

    ``allow_clocked_v3_forensics`` exists only to read the already-sealed v3
    diagnostic pilot. It never promotes that protocol to v4 qualification.
    """
    if not isinstance(root, str) or not root or root.startswith("/") or ".." in root.split("/"):
        raise ValueError("gecko_verifier_root")
    schemas = {row.get("schema") for row in intents if isinstance(row, dict)}
    if len(schemas) > 1:
        raise ValueError("gecko_intent_contract_mixed")
    allowed_schemas = {INTENT_SCHEMA_V4}
    if allow_clocked_v3_forensics:
        allowed_schemas.add(INTENT_SCHEMA_CLOCKED_V3)

    intent_by_id: dict[str, dict[str, Any]] = {}
    transformed: list[dict[str, Any]] = []
    referenced: set[str] = set()
    for intent in intents:
        if not isinstance(intent, dict) or set(intent) != _INTENT_FIELDS:
            raise ValueError("gecko_intent_v4_shape")
        if intent["schema"] not in allowed_schemas:
            raise ValueError("gecko_intent_v4_schema")
        if (
            intent["provider_charge_units"] is not None
            or intent["provider_charge_status"] != "unknown_unverified"
            or type(intent["decision_ns"]) is not int
            or type(intent["decision_monotonic_ns"]) is not int
            or not isinstance(intent["request_id"], str)
        ):
            raise ValueError("gecko_intent_v4_semantics")
        core = dict(intent)
        claimed = core.pop("intent_sha256")
        if claimed != sha256(canonical(core)):
            raise ValueError("gecko_intent_v4_hash")

        request = intent["request"]
        if not isinstance(request, dict) or type(request.get("bucket_close_ns")) is not int:
            raise ValueError("gecko_intent_v4_request")
        close_ns = request["bucket_close_ns"]
        expected_prefix = f"{root}/clocks/{close_ns}-"
        clock_path = intent["clock_path"]
        if (
            not isinstance(clock_path, str)
            or not clock_path.startswith(expected_prefix)
            or _CLOCK_NAME.fullmatch(clock_path[len(root) + 1 :]) is None
        ):
            raise ValueError("gecko_intent_v4_clock_path")
        match = _CLOCK_NAME.fullmatch(clock_path[len(root) + 1 :])
        assert match is not None
        if int(match.group(1)) != close_ns:
            raise ValueError("gecko_intent_v4_clock_bucket")
        clock_raw = clock_files.get(clock_path)
        if not isinstance(clock_raw, bytes) or sha256(clock_raw) != intent["clock_sha256"]:
            raise ValueError("gecko_intent_v4_clock_hash")
        try:
            record = json.loads(clock_raw, object_pairs_hook=_unique_object)
            if canonical(record) != clock_raw:
                raise ValueError("gecko_clock_not_canonical")
            validate_progress(
                record,
                wall_ns=intent["decision_ns"],
                monotonic_ns=intent["decision_monotonic_ns"],
            )
        except (ClockError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("gecko_intent_v4_clock_progress") from exc
        referenced.add(clock_path)
        if intent["request_id"] in intent_by_id:
            raise ValueError("gecko_intent_v4_duplicate")
        intent_by_id[intent["request_id"]] = intent

        # The exact v4 object and its original digest were verified above. Reuse
        # the mature v3 semantic replay only after preserving terminal coupling.
        legacy = {key: value for key, value in intent.items() if key not in _CLOCK_FIELDS}
        legacy["schema"] = INTENT_SCHEMA_CLOCKED_V3
        legacy_core = dict(legacy)
        legacy_core.pop("intent_sha256")
        legacy["intent_sha256"] = sha256(canonical(legacy_core))
        transformed.append(legacy)

    if set(clock_files) != referenced:
        raise ValueError("gecko_clock_file_closure")
    transformed_terminals: list[dict[str, Any]] = []
    for terminal in terminals:
        if not isinstance(terminal, dict):
            raise ValueError("gecko_terminal_v4_shape")
        original = intent_by_id.get(terminal.get("request_id"))
        if original is None or terminal.get("intent_sha256") != original["intent_sha256"]:
            raise ValueError("gecko_terminal_v4_intent_binding")
        row = dict(terminal)
        receipt = row.get("receipt_ns")
        availability = row.get("availability_ns")
        has_body = row.get("response_sha256") is not None
        if has_body:
            if type(receipt) is not int or type(availability) is not int or availability < receipt:
                raise ValueError("gecko_terminal_v4_availability_order")
        elif availability is not None:
            raise ValueError("gecko_terminal_v4_availability_without_capture")
        if row.get("status") in {STATUS_PROVIDER_ERROR, STATUS_TRANSPORT}:
            if row.get("cooldown_until_ns") is not None or row.get("cooldown_reason") is not None:
                raise ValueError("gecko_terminal_v4_transient_policy")
        if row.get("status") in {STATUS_RECEIVED, STATUS_EMPTY}:
            body = raw_responses.get(row["request_id"])
            if not isinstance(body, bytes):
                raise ValueError("gecko_terminal_v4_success_raw")
            parsed = parse_response(body, original["request"]["bucket_close_ns"], receipt)
            expected_bars = []
            for bar in parsed:
                projected_bar = dict(bar)
                source_available = projected_bar.pop("source_available_ns", None)
                projected_bar["provider_receipt_ns"] = source_available
                projected_bar["provider_receipt_semantics"] = (
                    "transport_receipt_only_not_decision_eligibility"
                )
                expected_bars.append(projected_bar)
            if row.get("bars") != expected_bars:
                raise ValueError("gecko_terminal_v4_bar_replay")
            row["bars"] = parsed
        projected = next(
            item for item in transformed if item["request_id"] == terminal["request_id"]
        )
        row["intent_sha256"] = projected["intent_sha256"]
        # v3 replay predates post-fsync availability. Validate the real order
        # above, then project only that field for its older receipt-equality rule.
        if has_body:
            row["availability_ns"] = receipt
        transformed_terminals.append(row)

    report = _verify_semantics(
        policy,
        transformed,
        transformed_terminals,
        raw_responses,
        skips=skips,
        scheduled_closes_ns=scheduled_closes_ns,
        dispatch_records=dispatch_records,
        clear_transient_cooldowns=True,
    )
    report["intent_contract"] = (
        "gecko-intent-v3-clock-bound-diagnostic"
        if allow_clocked_v3_forensics
        and any(row["schema"] == INTENT_SCHEMA_CLOCKED_V3 for row in intents)
        else INTENT_SCHEMA_V4
    )
    report["clock_files"] = len(referenced)
    bucket_closes = sorted(
        set(scheduled_closes_ns or ()) | {row["request"]["bucket_close_ns"] for row in intents}
    )
    pool_ids = {row["pool_id"] for row in policy["pools"]}
    observed = {(row["request"]["pool_id"], row["request"]["bucket_close_ns"]) for row in intents}
    missing = sorted(
        (pool_id, close_ns)
        for close_ns in bucket_closes
        for pool_id in pool_ids
        if (pool_id, close_ns) not in observed
    )
    report["missing_pool_buckets_unknown"] = [
        {"pool_id": pool_id, "bucket_close_ns": close_ns} for pool_id, close_ns in missing
    ]
    report["release_qualification_allowed"] = (
        report["intent_contract"] == INTENT_SCHEMA_V4
        and bool(bucket_closes)
        and not missing
        and report["unresolved_spent_intents"] == 0
        and report["terminal_count"] == report["intents"]
        and report["dispatch_records"] == report["intents"]
    )
    if not report["release_qualification_allowed"]:
        report["status"] = "diagnostic_only"
    return report
