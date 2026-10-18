"""One-bucket, local-only Gecko capture watchdog.

The callback never contacts a market provider. Before its fixed deadline it is
read-only. At the deadline it stops the collector, proves process quiescence,
replays immutable capture/global-usage evidence, and may create one diagnostic
receipt under a pre-existing descriptor-bound diagnostics directory.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from tools.brain_senses_external_writer_v1 import canonical_json

SCHEMA = "brain-senses-gecko-capture-watchdog-v3"
VERSION = 3
NS = 1_000_000_000
INTERVAL_SECONDS = 15
DEADLINE_AFTER_CLOSE_NS = 330 * NS
REQUEST_START_BEFORE_CLOSE_NS = 270 * NS
AVAILABILITY_BEFORE_CLOSE_NS = 300 * NS
WATCHDOG_LABEL = "ai.memecoin.brain.senses.gecko-capture-watchdog-v3"
COLLECTOR_LABEL = "ai.memecoin.brain.senses.gecko-capture-only-v5"


class WatchdogError(ValueError):
    """The watchdog could not safely complete its local-only audit."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def deadline_ns(bucket_close_ns: int) -> int:
    if type(bucket_close_ns) is not int or bucket_close_ns <= 0:
        raise WatchdogError("watchdog_bucket_close_invalid")
    return bucket_close_ns + DEADLINE_AFTER_CLOSE_NS


def _ids(rows: Sequence[Mapping[str, Any]], key: str) -> set[str]:
    values = [row.get(key) for row in rows]
    if (
        len(values) != 6
        or any(type(value) is not str or not value for value in values)
        or len(set(values)) != 6
    ):
        raise WatchdogError("watchdog_capture_identity_set_invalid")
    return set(cast(list[str], values))


def evaluate_final_audit(
    *,
    bucket_close_ns: int,
    observed_now_ns: int,
    collector_absent: bool,
    process_quiescent: bool,
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Produce a diagnostic verdict from independently replayed metadata only."""
    if type(observed_now_ns) is not int or observed_now_ns < deadline_ns(bucket_close_ns):
        raise WatchdogError("watchdog_deadline_not_reached")
    if collector_absent is not True or process_quiescent is not True:
        raise WatchdogError("watchdog_collector_not_quiescent")
    if (
        audit.get("capture_status") != "verified_prefix"
        or audit.get("inventory_status") != "verified"
        or audit.get("watchdog_provider_io") is not False
        or audit.get("pending_coordinator_reservations") != 0
        or audit.get("unresolved_count") != 0
        or audit.get("stop_reason") is not None
        or type(audit.get("root_inventory_sha256")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", audit["root_inventory_sha256"]) is None
        or any(
            type(audit.get(field)) is not str or re.fullmatch(r"[0-9a-f]{64}", audit[field]) is None
            for field in (
                "plan_sha256",
                "capture_prefix_sha256",
                "capture_verifier_sha256",
                "source_manifest_sha256",
                "release_sha256",
            )
        )
    ):
        raise WatchdogError("watchdog_independent_replay_failed")
    plan = audit.get("plan")
    if type(plan) is not dict or plan.get("bucket_close_ns") != bucket_close_ns:
        raise WatchdogError("watchdog_plan_binding_mismatch")
    selected = plan.get("selection", {}).get("selected_pool_ids")
    if (
        type(selected) is not list
        or len(selected) != 6
        or any(type(pool_id) is not str or not pool_id for pool_id in selected)
        or len(set(selected)) != 6
    ):
        raise WatchdogError("watchdog_plan_slot_set_invalid")
    intents = audit.get("intents")
    dispatches = audit.get("dispatches")
    results = audit.get("results")
    confirmations = audit.get("confirmations")
    skips = audit.get("skips")
    if any(type(rows) is not list for rows in (intents, dispatches, results, confirmations, skips)):
        raise WatchdogError("watchdog_capture_rows_invalid")
    intent_rows = cast(list[Mapping[str, Any]], intents)
    dispatch_rows = cast(list[Mapping[str, Any]], dispatches)
    result_rows = cast(list[Mapping[str, Any]], results)
    confirmation_rows = cast(list[Mapping[str, Any]], confirmations)
    skip_rows = cast(list[Any], skips)
    if skip_rows:
        raise WatchdogError("watchdog_capture_has_skips")
    expected = _ids(intent_rows, "request_id")
    if any(
        _ids(rows, "request_id") != expected
        for rows in (dispatch_rows, result_rows, confirmation_rows)
    ):
        raise WatchdogError("watchdog_capture_prefix_incomplete")
    intent_by_id = {row["request_id"]: row for row in intent_rows}
    dispatch_by_id = {row["request_id"]: row for row in dispatch_rows}
    result_by_id = {row["request_id"]: row for row in result_rows}
    confirmation_by_id = {row["request_id"]: row for row in confirmation_rows}
    if {row.get("pool_id") for row in intent_rows} != set(selected):
        raise WatchdogError("watchdog_capture_pool_set_mismatch")
    for request_id in expected:
        intent = intent_by_id[request_id]
        dispatch = dispatch_by_id[request_id]
        result = result_by_id[request_id]
        confirmation = confirmation_by_id[request_id]
        dispatch_ns = dispatch.get("dispatch_ns")
        request_started_ns = result.get("request_started_ns")
        availability_ns = confirmation.get("availability_ns")
        if (
            dispatch.get("intent_sha256") != intent.get("intent_sha256")
            or result.get("intent_sha256") != intent.get("intent_sha256")
            or confirmation.get("outcome_sha256") != sha256(canonical_json(result))
            or result.get("status") not in {"received", "empty_response"}
            or result.get("http_status") != 200
            or type(dispatch_ns) is not int
            or not bucket_close_ns + 60 * NS
            <= dispatch_ns
            < bucket_close_ns + REQUEST_START_BEFORE_CLOSE_NS
            or type(request_started_ns) is not int
            or dispatch_ns > request_started_ns
            or request_started_ns >= bucket_close_ns + REQUEST_START_BEFORE_CLOSE_NS
            or type(availability_ns) is not int
            or availability_ns >= bucket_close_ns + AVAILABILITY_BEFORE_CLOSE_NS
            or type(result.get("receipt_ns")) is not int
            or availability_ns < result["receipt_ns"]
        ):
            raise WatchdogError("watchdog_capture_attempt_not_timely_or_durable")
    core = {
        "schema": SCHEMA,
        "schema_version": VERSION,
        "bucket_close_ns": bucket_close_ns,
        "deadline_ns": deadline_ns(bucket_close_ns),
        "observed_ns": observed_now_ns,
        "collector_absent": True,
        "process_quiescent": True,
        "capture_status": "verified_prefix",
        "inventory_status": "verified",
        "root_inventory_sha256": audit["root_inventory_sha256"],
        "capture_verifier_sha256": audit["capture_verifier_sha256"],
        "source_manifest_sha256": audit["source_manifest_sha256"],
        "release_sha256": audit["release_sha256"],
        "plan_sha256": audit.get("plan_sha256"),
        "capture_prefix_sha256": audit.get("capture_prefix_sha256"),
        "intent_count": len(intent_rows),
        "dispatch_count": len(dispatch_rows),
        "result_count": len(result_rows),
        "confirmation_count": len(confirmation_rows),
        "skip_count": 0,
        "provider_io": False,
        "watchdog_provider_io": False,
        "dispatch_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "continuous_successor_required": True,
    }
    return {**core, "receipt_sha256": sha256(canonical_json(core))}


def run_watchdog_once(
    *,
    bucket_close_ns: int,
    now_ns: int,
    bootout_collector: Callable[[], Any],
    await_process_quiescence: Callable[[], bool],
    replay_under_locks: Callable[[], Mapping[str, Any]],
    publisher: Callable[[Mapping[str, Any]], str],
) -> dict[str, Any]:
    """At deadline, boot out first, then ask the locked replay and publisher."""
    due_at = deadline_ns(bucket_close_ns)
    if type(now_ns) is not int:
        raise WatchdogError("watchdog_clock_invalid")
    if now_ns < due_at:
        return {
            "schema": SCHEMA,
            "schema_version": VERSION,
            "status": "waiting_for_deadline",
            "deadline_ns": due_at,
            "provider_io": False,
            "dispatch_authorized": False,
        }
    bootout_collector()
    if await_process_quiescence() is not True:
        raise WatchdogError("watchdog_quiescence_unproven")
    audit = replay_under_locks()
    receipt = evaluate_final_audit(
        bucket_close_ns=bucket_close_ns,
        observed_now_ns=now_ns,
        collector_absent=True,
        process_quiescent=True,
        audit=audit,
    )
    receipt_path = publisher(receipt)
    return {**receipt, "receipt_path": receipt_path}


def publish_diagnostic_create_only(
    writer: Any,
    *,
    diagnostics_root: str,
    receipt: Mapping[str, Any],
    revalidate: Callable[[], Any],
) -> str:
    """Write only within an existing diagnostics directory; no mkdir or overwrite."""
    if (
        not diagnostics_root
        or diagnostics_root.startswith("/")
        or any(part in {"", ".", ".."} for part in diagnostics_root.split("/"))
        or receipt.get("schema") != SCHEMA
        or receipt.get("provider_io") is not False
        or receipt.get("dispatch_authorized") is not False
    ):
        raise WatchdogError("watchdog_diagnostic_binding_invalid")
    try:
        writer.list_names(diagnostics_root)
    except Exception as exc:
        raise WatchdogError("watchdog_diagnostics_root_unavailable") from exc
    path = f"{diagnostics_root}/{receipt['bucket_close_ns']}.json"
    payload = canonical_json(dict(receipt))
    revalidate()
    try:
        writer.atomic_write(path, payload, create_only=True)
    except Exception as write_exc:
        try:
            existing = writer.read_bytes(path, max_bytes=max(4096, len(payload)))
        except Exception as read_exc:
            raise write_exc from read_exc
        if existing != payload:
            raise WatchdogError("watchdog_diagnostic_collision") from write_exc
    revalidate()
    if writer.read_bytes(path, max_bytes=max(4096, len(payload))) != payload:
        raise WatchdogError("watchdog_diagnostic_readback")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Fail closed until the separately reviewed owner callback adapter exists."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-adapter-required", action="store_true", required=True)
    parser.parse_args(argv)
    sys.stderr.write("watchdog_owner_callback_adapter_unbound\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
