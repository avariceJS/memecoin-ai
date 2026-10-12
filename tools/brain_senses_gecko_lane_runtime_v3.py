"""Descriptor-bound host boundary for the prospective Gecko lane v3.

The module deliberately has no provider loader. A later, independently reviewed
release must provide a verified activation receipt and an in-process fetcher.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_research_storage import (  # noqa: E402
    _open_relative_directory,
    writer_guard,
)
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_external_clock_v1 as clock  # noqa: E402
from tools import brain_senses_gecko_lane_engine_v3 as engine  # noqa: E402
from tools import brain_senses_gecko_lane_live_sources_v2 as live_sources  # noqa: E402
from tools import brain_senses_gecko_lane_policy_v3 as offline  # noqa: E402
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    canonical_json,
    sha256,
)

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
SERVICE_ROOT = "services/gecko-market-v1"
V3_ROOT = f"{SERVICE_ROOT}/v3"
PILOT_ROOT_PREFIX = f"{SERVICE_ROOT}/v3-pilots"
SERVICE_LOCK = ".gecko-market-v1.lock"
SOURCE_BINDING_KEY = live_sources.MANIFEST_PATH
LIVE_PLAN_SCHEMA = "brain-senses-gecko-live-v3-plan"
ACTIVATION_SCHEMA = "brain-senses-gecko-live-v3-activation"
PILOT_AUTH_SCHEMA = "brain-senses-gecko-live-v3-pilot-authorization"
PILOT_RECEIPT_SCHEMA = "brain-senses-gecko-live-v3-pilot-receipt"
MAX_REQUEST_START_OFFSET_NS = 155 * offline.NS
MINIMUM_DURABLE_RESULT_SPACING_NS = 20 * offline.NS
MAX_LANE_DISPATCHES_PER_60_SECONDS = 3
TRANSIENT_TERMINAL_STATUSES = {engine.STATUS_PROVIDER_ERROR, engine.STATUS_TRANSPORT}
IMMEDIATE_STOP_STATUSES = {
    engine.STATUS_RATE_LIMITED,
    engine.STATUS_CLIENT_ERROR,
    engine.STATUS_MALFORMED,
    engine.STATUS_OVERSIZE,
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class GeckoRuntimeError(RuntimeError):
    """Fail-closed plan, persistence, coordinator, or clock error."""


def source_bindings() -> dict[str, str]:
    """Hash the complete runtime closure used by this independently versioned host."""
    names = (
        "tools/brain_senses_gecko_lane_runtime_v3.py",
        "tools/brain_senses_gecko_lane_engine_v3.py",
        "tools/brain_senses_gecko_lane_live_sources_v2.py",
        "tools/brain_senses_gecko_lane_policy_v3.py",
        "tools/brain_senses_gecko_lane_verify_v3.py",
        "tools/brain_senses_capacity_coordinator_v2.py",
        "tools/brain_senses_external_clock_v1.py",
        "tools/brain_senses_external_writer_v1.py",
        "src/memecoin_research_storage/__init__.py",
        "ops/launchd/ai.memecoin.brain.senses.gecko-lane-v3.plist",
    )
    return {name: sha256((REPO / name).read_bytes()) for name in names}


def _source_bindings_for_store(writer: DescriptorWriter, guard: Any) -> dict[str, str]:
    guard.revalidate()
    pinned = live_sources.verify_source_binding(
        writer,
        expected_root_id=guard.root_id,
        expected_volume_uuid=guard.volume_uuid,
    )
    guard.revalidate()
    return {**source_bindings(), SOURCE_BINDING_KEY: pinned.manifest_sha256}


def _verify_source_map_shape(sources: Mapping[str, Any]) -> None:
    expected_code = source_bindings()
    if (
        not isinstance(sources, Mapping)
        or set(sources) != {*expected_code, SOURCE_BINDING_KEY}
        or any(sources.get(name) != digest for name, digest in expected_code.items())
        or not _is_sha(sources.get(SOURCE_BINDING_KEY))
    ):
        raise GeckoRuntimeError("live_source_map")


def compile_live_plan(
    policy: dict[str, Any],
    *,
    pilot_id: str,
    root_id: str,
    volume_uuid: str,
    source_map: Mapping[str, str],
) -> dict[str, Any]:
    """Compile a versioned storage-bound plan; it grants no dispatch authority."""
    offline.verify_policy(policy)
    if not _is_pilot_id(pilot_id):
        raise GeckoRuntimeError("pilot_identity_invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", root_id):
        raise GeckoRuntimeError("root_identity_invalid")
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", volume_uuid.lower()):
        raise GeckoRuntimeError("volume_identity_invalid")
    if not source_map or any(
        not isinstance(k, str) or not _is_sha(v) for k, v in source_map.items()
    ):
        raise GeckoRuntimeError("source_map_invalid")
    plan: dict[str, Any] = {
        "schema": LIVE_PLAN_SCHEMA,
        "schema_version": 3,
        "pilot_id": pilot_id,
        "service_root": _pilot_root(pilot_id),
        "storage": {"root_id": root_id, "volume_uuid": volume_uuid.lower()},
        "policy_sha256": policy["policy_sha256"],
        "policy": policy,
        "source_sha256": dict(sorted(source_map.items())),
        "dispatch": {
            "bucket_seconds": 900,
            "window_start_offset_seconds": 60,
            "window_end_offset_seconds": 180,
            "latest_request_start_offset_seconds": 155,
            "minimum_after_durable_result_seconds": 20,
            "hard_requests_per_rolling_minute": MAX_LANE_DISPATCHES_PER_60_SECONDS,
            "global_research_cap_per_rolling_minute": 5,
            "clock_record_schema": clock.CLOCK_SCHEMA,
            "clock_max_age_seconds": 300,
            "pool_order": "rotate_frozen_selection_by_bucket_index",
            "restart_scope": "current_open_bucket_only_with_proven_no_intent",
            "calendar_expiry": "none",
        },
        "release_required": True,
        "activation_authorized": False,
        "provider_io": False,
    }
    plan["plan_sha256"] = sha256(canonical_json(plan))
    verify_live_plan(plan)
    return plan


def verify_live_plan(plan: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "schema_version",
        "pilot_id",
        "service_root",
        "storage",
        "policy_sha256",
        "policy",
        "source_sha256",
        "dispatch",
        "release_required",
        "activation_authorized",
        "provider_io",
        "plan_sha256",
    }
    if not isinstance(plan, Mapping) or set(plan) != required:
        raise GeckoRuntimeError("live_plan_shape")
    core = dict(plan)
    claimed = core.pop("plan_sha256")
    if claimed != sha256(canonical_json(core)):
        raise GeckoRuntimeError("live_plan_hash")
    storage = plan["storage"]
    if (
        plan["schema"] != LIVE_PLAN_SCHEMA
        or plan["schema_version"] != 3
        or not _is_pilot_id(plan["pilot_id"])
        or plan["service_root"] != _pilot_root(plan["pilot_id"])
        or not isinstance(storage, Mapping)
        or set(storage) != {"root_id", "volume_uuid"}
        or not re.fullmatch(r"[0-9a-f]{32}", str(storage["root_id"]))
        or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(storage["volume_uuid"])
        )
        or plan["activation_authorized"] is not False
        or plan["provider_io"] is not False
        or plan["release_required"] is not True
    ):
        raise GeckoRuntimeError("live_plan_binding")
    offline.verify_policy(dict(plan["policy"]))
    if plan["policy_sha256"] != plan["policy"]["policy_sha256"]:
        raise GeckoRuntimeError("live_policy_binding")
    expected_dispatch = {
        "bucket_seconds": 900,
        "window_start_offset_seconds": 60,
        "window_end_offset_seconds": 180,
        "latest_request_start_offset_seconds": 155,
        "minimum_after_durable_result_seconds": 20,
        "hard_requests_per_rolling_minute": MAX_LANE_DISPATCHES_PER_60_SECONDS,
        "global_research_cap_per_rolling_minute": 5,
        "clock_record_schema": clock.CLOCK_SCHEMA,
        "clock_max_age_seconds": 300,
        "pool_order": "rotate_frozen_selection_by_bucket_index",
        "restart_scope": "current_open_bucket_only_with_proven_no_intent",
        "calendar_expiry": "none",
    }
    if plan["dispatch"] != expected_dispatch:
        raise GeckoRuntimeError("live_dispatch_policy")
    sources = plan["source_sha256"]
    _verify_source_map_shape(sources)


def verify_activation(plan: Mapping[str, Any], activation: Mapping[str, Any]) -> None:
    """Validate review pins; this code never creates or infers an approval."""
    verify_live_plan(plan)
    if not isinstance(activation, Mapping) or set(activation) != {
        "schema",
        "schema_version",
        "pilot_id",
        "plan_sha256",
        "source_sha256",
        "root_id",
        "volume_uuid",
        "activation_authorized",
        "provider_io",
        "qualification_status",
    }:
        raise GeckoRuntimeError("activation_shape")
    storage = plan["storage"]
    if (
        activation["schema"] != ACTIVATION_SCHEMA
        or activation["schema_version"] != 3
        or activation["pilot_id"] != plan["pilot_id"]
        or activation["plan_sha256"] != plan["plan_sha256"]
        or activation["source_sha256"] != plan["source_sha256"]
        or activation["root_id"] != storage["root_id"]
        or activation["volume_uuid"] != storage["volume_uuid"]
        or activation["activation_authorized"] is not True
        or activation["provider_io"] is not True
        or activation["qualification_status"] != "passed"
        or activation["source_sha256"] != plan["source_sha256"]
    ):
        raise GeckoRuntimeError("activation_binding")
    _verify_source_map_shape(plan["source_sha256"])


def verify_pilot_authorization(
    plan: Mapping[str, Any], authorization: Mapping[str, Any], bucket_close_ns: int
) -> None:
    verify_live_plan(plan)
    expected = {
        "schema",
        "schema_version",
        "pilot_id",
        "plan_sha256",
        "source_sha256",
        "root_id",
        "volume_uuid",
        "mode",
        "bucket_close_ns",
        "status",
    }
    if not isinstance(authorization, Mapping) or set(authorization) != expected:
        raise GeckoRuntimeError("pilot_authorization_shape")
    if (
        authorization["schema"] != PILOT_AUTH_SCHEMA
        or authorization["schema_version"] != 3
        or authorization["pilot_id"] != plan["pilot_id"]
        or authorization["plan_sha256"] != plan["plan_sha256"]
        or authorization["source_sha256"] != plan["source_sha256"]
        or authorization["root_id"] != plan["storage"]["root_id"]
        or authorization["volume_uuid"] != plan["storage"]["volume_uuid"]
        or authorization["mode"] != "one_shot"
        or authorization["bucket_close_ns"] != bucket_close_ns
        or authorization["status"] != "approved"
    ):
        raise GeckoRuntimeError("pilot_authorization_binding")


def verify_pilot_receipt(
    plan: Mapping[str, Any], receipt: Mapping[str, Any], receipt_sha256: str
) -> None:
    verify_live_plan(plan)
    expected = {
        "schema",
        "schema_version",
        "pilot_id",
        "plan_sha256",
        "source_sha256",
        "bucket_close_ns",
        "verification_status",
        "verifier_output_sha256",
        "result_sha256",
        "summary",
        "prefix",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != expected:
        raise GeckoRuntimeError("pilot_receipt_shape")
    if (
        not _is_sha(receipt_sha256)
        or receipt["schema"] != PILOT_RECEIPT_SCHEMA
        or receipt["schema_version"] != 3
        or receipt["pilot_id"] != plan["pilot_id"]
        or receipt["plan_sha256"] != plan["plan_sha256"]
        or receipt["source_sha256"] != plan["source_sha256"]
        or type(receipt["bucket_close_ns"]) is not int
        or receipt["verification_status"] != "passed"
        or not isinstance(receipt["summary"], Mapping)
        or not _is_sha(receipt["verifier_output_sha256"])
        or receipt["verifier_output_sha256"] != sha256(canonical_json(dict(receipt["summary"])))
        or not isinstance(receipt["prefix"], Mapping)
        or not _is_sha(receipt["result_sha256"])
        or receipt["result_sha256"] != sha256(canonical_json(dict(receipt["prefix"])))
        or receipt["summary"].get("bucket_close_ns") != receipt["bucket_close_ns"]
        or receipt["summary"].get("pilot_outcome")
        not in {"review_candidate", "manual_rearm_required"}
        or type(receipt["summary"].get("possibly_spent")) is not int
    ):
        raise GeckoRuntimeError("pilot_receipt_binding")


def pilot_prefix(
    store: DescriptorLaneStore, plan: Mapping[str, Any], bucket_close_ns: int
) -> dict[str, Any]:
    """Return an immutable artifact manifest for one pilot bucket.

    Later scheduled rows may be appended, so the release gate binds this prefix
    rather than a summary whose aggregate counts necessarily change over time.
    """
    intents: dict[str, dict[str, Any]] = {}
    intent_hashes: dict[str, str] = {}
    for name in store.files("intents"):
        if not name.endswith(".json"):
            raise GeckoRuntimeError("pilot_intent_inventory_unexpected")
        request_id = name[:-5]
        raw = store.writer.read_bytes(store._path(f"intents/{name}"), max_bytes=256 * 1024)
        row = store.read_json(f"intents/{name}")
        if row.get("request_id") != request_id:
            raise GeckoRuntimeError("pilot_intent_identity_mismatch")
        intents[request_id] = row
        intent_hashes[request_id] = sha256(raw)

    results: dict[str, tuple[dict[str, Any], str]] = {}
    for name in store.files("results"):
        if not name.endswith(".json"):
            raise GeckoRuntimeError("pilot_result_inventory_unexpected")
        request_id = name[:-5]
        raw = store.writer.read_bytes(store._path(f"results/{name}"), max_bytes=256 * 1024)
        results[request_id] = (store.read_json(f"results/{name}"), sha256(raw))

    dispatch_rows = store.read_dispatch_records()
    confirmations = store.read_confirmation_records()
    confirmation_by_id = {row["request_id"]: row for row in confirmations}
    pilot_ids = sorted(
        request_id
        for request_id, row in intents.items()
        if row.get("request", {}).get("bucket_close_ns") == bucket_close_ns
    )
    artifacts: list[dict[str, Any]] = []
    for request_id in pilot_ids:
        result_pair = results.get(request_id)
        if result_pair is None:
            result_sha = None
        else:
            result_sha = result_pair[1]
        capture_path = f"captures/{request_id}.bin"
        capture_sha = (
            sha256(store.writer.read_bytes(store._path(capture_path), max_bytes=32 * 1024 * 1024))
            if store.writer.exists(store._path(capture_path))
            else None
        )
        confirmation = confirmation_by_id.get(request_id)
        artifacts.append(
            {
                "request_id": request_id,
                "intent_sha256": intent_hashes[request_id],
                "result_sha256": result_sha,
                "capture_sha256": capture_sha,
                "confirmation_sha256": (
                    confirmation["record_sha256"] if confirmation is not None else None
                ),
            }
        )
    pilot_set = set(pilot_ids)
    dispatch_hashes = [
        row["record_sha256"] for row in dispatch_rows if row.get("request_id") in pilot_set
    ]
    skip_entries = []
    for name in store.files("skips"):
        if name.startswith(f"{bucket_close_ns}-") and name.endswith(".json"):
            raw = store.writer.read_bytes(store._path(f"skips/{name}"), max_bytes=64 * 1024)
            skip_entries.append({"path": f"skips/{name}", "sha256": sha256(raw)})
    skip_entries.sort(key=lambda row: row["path"])
    return {
        "schema": "brain-senses-gecko-v3-pilot-prefix",
        "plan_sha256": plan["plan_sha256"],
        "bucket_close_ns": bucket_close_ns,
        "request_ids": pilot_ids,
        "artifacts": artifacts,
        "dispatch_record_sha256": dispatch_hashes,
        "skip_artifacts": skip_entries,
    }


def verify_pilot_bucket(
    store: DescriptorLaneStore,
    plan: Mapping[str, Any],
    bucket_close_ns: int,
    *,
    require_clock: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute the pilot qualification from immutable intents and raw artifacts."""
    verified = verify_live_store(store, plan, require_clock=require_clock)
    prefix = pilot_prefix(store, plan, bucket_close_ns)
    bucket_intents = {
        request_id: store.read_json(f"intents/{request_id}.json")
        for request_id in verified_intent_ids(store)
        if store.read_json(f"intents/{request_id}.json")["request"]["bucket_close_ns"]
        == bucket_close_ns
    }
    intended_pools = {row["pool_id"] for row in plan["policy"]["pools"]}
    actual_pools = {row["request"]["pool_id"] for row in bucket_intents.values()}
    reason: str | None = None
    if verified["possibly_spent"] != 0:
        reason = "pilot_or_service_unresolved_intent"
    dispatch_state = store.dispatch_state()
    if dispatch_state is not None and dispatch_state["manual_review_required"]:
        reason = reason or "pilot_or_service_manual_review_latched"
    if (
        len(bucket_intents) != 4
        or len(bucket_intents) != len(actual_pools)
        or actual_pools != intended_pools
    ):
        reason = "pilot_intent_set_incomplete"

    request_ids = set(bucket_intents)
    dispatch_rows = [
        row for row in store.read_dispatch_records() if row.get("request_id") in request_ids
    ]
    if reason is None and (
        len(dispatch_rows) != 4 or {row["request_id"] for row in dispatch_rows} != request_ids
    ):
        reason = "pilot_dispatch_set_incomplete"

    results = {
        row["request_id"]: row
        for row in store.result_rows()
        if row.get("request_id") in request_ids
    }
    confirmations = {row["request_id"]: row for row in store.read_confirmation_records()}
    eligible_new_open_count = 0
    empty_response_count = 0
    success_set = {engine.STATUS_RECEIVED, engine.STATUS_EMPTY}
    for request_id, intent in bucket_intents.items():
        result = results.get(request_id)
        confirmation = confirmations.get(request_id)
        if (
            result is None
            or result.get("status") not in success_set
            or result.get("http_status") != 200
            or not _is_sha(result.get("response_sha256"))
            or confirmation is None
        ):
            reason = reason or "pilot_success_artifact_missing"
            continue
        capture_path = store._path(f"captures/{request_id}.bin")
        raw = store.writer.read_bytes(capture_path, max_bytes=32 * 1024 * 1024)
        request = intent["request"]
        reparsed = _runtime_bars(
            engine.parse_response(raw, request["bucket_close_ns"], result["receipt_ns"])
        )
        if (
            sha256(raw) != result["response_sha256"]
            or len(raw) != result["response_bytes"]
            or reparsed != result.get("bars")
            or result.get("availability_ns") is None
            or confirmation.get("confirmed_ns", 0) < result["availability_ns"]
        ):
            reason = reason or "pilot_raw_reparse_mismatch"
            continue
        if result["status"] == engine.STATUS_EMPTY:
            if reparsed:
                reason = reason or "pilot_empty_status_mismatch"
        elif not reparsed:
            reason = reason or "pilot_received_status_mismatch"
        eligible_new_open_count += sum(
            row["disposition"] == "eligible_new_open" for row in reparsed
        )
        empty_response_count += result["status"] == engine.STATUS_EMPTY

    if reason is None and eligible_new_open_count < 1:
        reason = "pilot_no_eligible_new_open"
    if reason is None and any(
        name.startswith(f"{bucket_close_ns}-") for name in store.files("skips")
    ):
        reason = "pilot_skip_present"
    qualification = {
        "schema": "brain-senses-gecko-v3-pilot-qualification",
        "status": "passed" if reason is None else "diagnostic_only",
        "reason": "four_pool_pilot_qualified" if reason is None else reason,
        "bucket_close_ns": bucket_close_ns,
        "intents": len(bucket_intents),
        "dispatches": len(dispatch_rows),
        "http_200_captures": sum(
            row.get("http_status") == 200 and row.get("response_sha256") is not None
            for row in results.values()
        ),
        "durable_confirmations": sum(request_id in confirmations for request_id in request_ids),
        "empty_responses": empty_response_count,
        "eligible_new_open": eligible_new_open_count,
        "skips": sum(name.startswith(f"{bucket_close_ns}-") for name in store.files("skips")),
        "verified_store": verified["status"],
        "clock_verified": verified["clock_verified"],
        "provider_io": False,
        "economic_admission": False,
    }
    return qualification, prefix


def verified_intent_ids(store: DescriptorLaneStore) -> set[str]:
    return {name[:-5] for name in store.files("intents") if name.endswith(".json")}


def make_capacity_coordinator(
    writer: DescriptorWriter, guard: Any, *, now_ns: Callable[[], int]
) -> capacity.CapacityCoordinator:
    """Bind coordinator v2 read-only to its existing policy; never initialize it here."""
    root_stat = os.fstat(writer.root_fd)
    roots = {
        "helius": "services/external-rolling-v5",
        "alchemy": "services/alchemy-sentinel-v2",
        "gecko": SERVICE_ROOT,
    }
    locks = {
        "helius": ".external-rolling-v4.lock",
        "alchemy": ".alchemy-sentinel-v2.lock",
        "gecko": SERVICE_LOCK,
    }
    allocations = {
        "helius": ("services/external-rolling-v4", "services/external-rolling-v5"),
        "alchemy": ("services/alchemy-sentinel-v2",),
        "gecko": (SERVICE_ROOT,),
        "coordinator": (capacity.COORDINATOR_ROOT,),
    }

    def verify_root(_writer: capacity.Writer) -> None:
        guard.revalidate()

    source = capacity.DescriptorCapacitySource(
        allocation_roots=allocations, verify_root=verify_root
    )
    coordinator = capacity.CapacityCoordinator(
        writer,
        source=source,
        service_roots=roots,
        service_lock_paths=locks,
        allocation_roots=allocations,
        root_binding={
            "root_id": guard.root_id,
            "volume_uuid": guard.volume_uuid.lower(),
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        },
        now_ns=now_ns,
    )
    required_files = {
        capacity.POLICY_PATH,
        capacity.LEDGER_PATH,
        capacity.LOCK_PATH,
        *locks.values(),
    }
    required_dirs = {
        SERVICE_ROOT,
        *roots.values(),
        *[path for paths in allocations.values() for path in paths],
    }
    try:
        missing_or_wrong = any(
            not stat.S_ISREG(writer.stat(path).st_mode) for path in required_files
        )
        missing_or_wrong = missing_or_wrong or any(
            not stat.S_ISDIR(writer.stat(path).st_mode) for path in required_dirs
        )
    except OSError:
        missing_or_wrong = True
    if missing_or_wrong:
        raise GeckoRuntimeError("coordinator_not_initialized")
    if writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000) != coordinator.policy_bytes:
        raise GeckoRuntimeError("coordinator_policy_mismatch")
    return coordinator


def bucket_close_for(now_ns: int) -> int:
    if type(now_ns) is not int or now_ns < 0:
        raise GeckoRuntimeError("clock_invalid")
    return now_ns - (now_ns % (offline.BUCKET_SECONDS * offline.NS))


def rotated_pool_order(policy: Mapping[str, Any], bucket_close_ns: int) -> list[str]:
    offline.verify_policy(dict(policy))
    if type(bucket_close_ns) is not int or bucket_close_ns % (offline.BUCKET_SECONDS * offline.NS):
        raise GeckoRuntimeError("bucket_identity_invalid")
    pools = [row["pool_id"] for row in policy["pools"]]
    offset = (bucket_close_ns // (offline.BUCKET_SECONDS * offline.NS)) % len(pools)
    return pools[offset:] + pools[:offset]


def current_bucket_for_tick(now_ns: int) -> int | None:
    """Return the single open quarter-hour bucket, never a historical catch-up bucket."""
    close_ns = bucket_close_for(now_ns)
    start_ns, end_ns = engine.dispatch_window(close_ns)
    if now_ns < start_ns or now_ns >= end_ns:
        return None
    return close_ns


def _within_lane_budget(store: DescriptorLaneStore, checked_ns: int, request_id: str) -> bool:
    """Conservatively admit one intent against local UTC-day and rolling-30d caps."""
    if store._policy is None:
        raise GeckoRuntimeError("verified_plan_not_bound")
    protocol = store._policy["protocol"]
    day_ns = 86_400 * offline.NS
    rolling_start = checked_ns - 30 * day_ns
    today_start = checked_ns - checked_ns % day_ns
    intents = [
        store.read_json(f"intents/{name}")
        for name in store.files("intents")
        if name.endswith(".json")
    ]
    results = {row["request_id"]: row for row in store.result_rows()}
    prospective_present = any(row["request_id"] == request_id for row in intents)
    if not prospective_present:
        intents.append({"request_id": request_id, "decision_ns": checked_ns})
    utc_day = [row for row in intents if today_start <= row["decision_ns"] <= checked_ns]
    rolling = [row for row in intents if rolling_start <= row["decision_ns"] <= checked_ns]
    if len(utc_day) > protocol["maximum_intents_per_utc_day"]:
        return False
    if len(rolling) > protocol["maximum_intents_per_rolling_30d"]:
        return False

    def charged_bytes(rows: list[dict[str, Any]]) -> int:
        total = 0
        for row in rows:
            result = results.get(row["request_id"])
            amount = (
                result["response_bytes"]
                if result is not None and result.get("response_sha256") is not None
                else protocol["maximum_raw_response_bytes"]
            )
            if type(amount) is not int or amount < 0:
                raise GeckoRuntimeError("lane_budget_result_invalid")
            total += amount
        return total

    return bool(
        charged_bytes(utc_day) <= protocol["maximum_raw_bytes_per_utc_day"]
        and charged_bytes(rolling) <= protocol["maximum_raw_bytes_per_rolling_30d"]
    )


class DescriptorLaneStore:
    """Small descriptor-only v3 journal over one already-open Kingston data-root fd."""

    def __init__(self, writer: DescriptorWriter, *, guard: Any, root: str = V3_ROOT) -> None:
        self.writer = writer
        self.guard = guard
        self.root = root
        self._policy: dict[str, Any] | None = None

    def _path(self, name: str) -> str:
        return f"{self.root}/{name}"

    def initialize(self, plan: Mapping[str, Any], activation: Mapping[str, Any]) -> None:
        verify_activation(plan, activation)
        self._initialize_paths(plan)
        self._create_exact("activation.json", activation)

    def initialize_pilot(
        self,
        plan: Mapping[str, Any],
        authorization: Mapping[str, Any],
        bucket_close_ns: int,
    ) -> None:
        """Create the exact plan and one-bucket pilot authorization, never a schedule release."""
        verify_live_plan(plan)
        verify_pilot_authorization(plan, authorization, bucket_close_ns)
        try:
            self.writer.stat(self.root)
        except FileNotFoundError:
            pass
        else:
            raise GeckoRuntimeError("pilot_subroot_already_exists")
        self._initialize_paths(plan)
        self._create_exact("pilot-authorization.json", authorization)

    def _initialize_paths(self, plan: Mapping[str, Any]) -> None:
        verify_live_plan(plan)
        if plan["service_root"] != self.root:
            raise GeckoRuntimeError("pilot_store_root_mismatch")
        self._verify_guard(plan)
        for name in (
            SERVICE_ROOT,
            PILOT_ROOT_PREFIX,
            self.root,
            f"{self.root}/intents",
            f"{self.root}/captures",
            f"{self.root}/results",
            f"{self.root}/skips",
            f"{self.root}/clocks",
        ):
            self.guard.revalidate()
            self.writer.mkdir(name)
        runner_lock = self._path(".runner.lock")
        if not self.writer.exists(runner_lock):
            self.writer.create(runner_lock, b"gecko-lane-v3-runner-lock\n")
        self._create_exact("plan.json", plan)
        self.bind_verified_plan(plan)
        self._verify_guard(plan)

    def bind_verified_plan(self, plan: Mapping[str, Any]) -> None:
        """Restore policy state only from the exact persisted, independently verified plan."""
        verify_live_plan(plan)
        if plan["service_root"] != self.root:
            raise GeckoRuntimeError("pilot_store_root_mismatch")
        stored = self.read_json("plan.json")
        if stored != dict(plan):
            raise GeckoRuntimeError("stored_plan_mismatch")
        self._policy = dict(plan["policy"])

    def _create_exact(self, name: str, value: Mapping[str, Any]) -> None:
        path = self._path(name)
        payload = canonical_json(dict(value))
        if self.writer.exists(path):
            if self.writer.read_bytes(path) != payload:
                raise GeckoRuntimeError("plan_artifact_conflict")
        else:
            self.guard.revalidate()
            self.writer.create(path, payload)

    def _verify_guard(self, plan: Mapping[str, Any]) -> None:
        self.guard.revalidate()
        writer_stat = os.fstat(self.writer.root_fd)
        guard_stat = os.fstat(self.guard.mount_fd)
        config = getattr(self.guard, "config", None)
        data_root = getattr(config, "data_root", None)
        if data_root:
            data_root_fd = _open_relative_directory(self.guard.mount_fd, data_root)
            try:
                guard_stat = os.fstat(data_root_fd)
            finally:
                os.close(data_root_fd)
        if (
            self.guard.root_id != plan["storage"]["root_id"]
            or self.guard.volume_uuid.lower() != plan["storage"]["volume_uuid"]
            or (writer_stat.st_dev, writer_stat.st_ino) != (guard_stat.st_dev, guard_stat.st_ino)
        ):
            raise GeckoRuntimeError("storage_binding_changed")

    def files(self, directory: str) -> tuple[str, ...]:
        return self.writer.list_names(self._path(directory))

    def read_json(self, relative: str) -> dict[str, Any]:
        raw = self.writer.read_bytes(self._path(relative), max_bytes=8 * 1024 * 1024)
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise GeckoRuntimeError("journal_json_invalid") from exc
        if not isinstance(value, dict):
            raise GeckoRuntimeError("journal_shape_invalid")
        if canonical_json(value) != raw:
            raise GeckoRuntimeError("journal_not_canonical")
        return value

    def read_dispatch_records(self) -> list[dict[str, Any]]:
        path = self._path("dispatch.jsonl")
        if not self.writer.exists(path):
            return []
        raw = self.writer.read_bytes(path, max_bytes=16 * 1024 * 1024)
        if raw and not raw.endswith(b"\n"):
            raise GeckoRuntimeError("dispatch_journal_partial")
        records = []
        for line in raw.splitlines():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise GeckoRuntimeError("dispatch_journal_row")
            records.append(item)
        engine.verify_dispatch_journal(records)
        return records

    def read_confirmation_records(self) -> list[dict[str, Any]]:
        path = self._path("result-confirmations.jsonl")
        if not self.writer.exists(path):
            return []
        raw = self.writer.read_bytes(path, max_bytes=8 * 1024 * 1024)
        if raw and not raw.endswith(b"\n"):
            raise GeckoRuntimeError("confirmation_journal_partial")
        records = [json.loads(line) for line in raw.splitlines()]
        previous = None
        for index, row in enumerate(records):
            core = dict(row)
            claimed = core.pop("record_sha256", None)
            if (
                set(row)
                != {
                    "schema",
                    "sequence",
                    "request_id",
                    "outcome_sha256",
                    "confirmed_ns",
                    "previous_sha256",
                    "record_sha256",
                }
                or row["schema"] != "brain-senses-gecko-result-confirmation-v3"
                or row["sequence"] != index
                or row["previous_sha256"] != previous
                or not _is_sha(claimed)
                or claimed != sha256(canonical_json(core))
                or type(row["confirmed_ns"]) is not int
            ):
                raise GeckoRuntimeError("confirmation_chain_invalid")
            previous = claimed
        return records

    def append_result_confirmation(
        self, request_id: str, outcome_sha256: str, confirmed_ns: int
    ) -> None:
        records = self.read_confirmation_records()
        row: dict[str, Any] = {
            "schema": "brain-senses-gecko-result-confirmation-v3",
            "sequence": len(records),
            "request_id": request_id,
            "outcome_sha256": outcome_sha256,
            "confirmed_ns": confirmed_ns,
            "previous_sha256": records[-1]["record_sha256"] if records else None,
        }
        row["record_sha256"] = sha256(canonical_json(row))
        self.guard.revalidate()
        self.writer.append(self._path("result-confirmations.jsonl"), canonical_json(row))

    def reconcile_result_confirmations(self, now_ns: Callable[[], int]) -> None:
        confirmed = {row["request_id"] for row in self.read_confirmation_records()}
        for result in self.result_rows():
            request_id = result["request_id"]
            if request_id in confirmed:
                continue
            raw = self.writer.read_bytes(self._path(f"results/{request_id}.json"))
            # Local crash recovery only: this can never replay provider I/O.
            self.append_result_confirmation(request_id, sha256(raw), now_ns())

    def latest_confirmation_ns(self) -> int | None:
        records = self.read_confirmation_records()
        return records[-1]["confirmed_ns"] if records else None

    def result_rows(self) -> list[dict[str, Any]]:
        rows = []
        for name in self.files("results"):
            if not name.endswith(".json"):
                raise GeckoRuntimeError("result_inventory_unexpected")
            rows.append(self.read_json(f"results/{name}"))
        return rows

    def intent_request_ids(self) -> set[str]:
        ids: set[str] = set()
        for name in self.files("intents"):
            if not name.endswith(".json"):
                raise GeckoRuntimeError("intent_inventory_unexpected")
            intent = self.read_json(f"intents/{name}")
            if intent.get("schema") != "brain-senses-gecko-intent-v3":
                raise GeckoRuntimeError("intent_artifact_invalid")
            request_id = intent.get("request_id")
            if not isinstance(request_id, str):
                raise GeckoRuntimeError("intent_request_id_invalid")
            ids.add(request_id)
        return ids

    def dispatch_state(self) -> dict[str, Any] | None:
        records = self.read_dispatch_records()
        summary = engine.verify_dispatch_journal(records)
        if summary is None:
            return None
        rows = self.result_rows()
        cooldowns = [row for row in rows if row.get("cooldown_until_ns") is not None]
        manual = any(
            row.get("manual_review_required") is True
            or row.get("cooldown_reason")
            in {"contract_access_review_required", "review_required_rate_limit"}
            or row.get("status")
            in {
                engine.STATUS_RATE_LIMITED,
                engine.STATUS_CLIENT_ERROR,
                engine.STATUS_MALFORMED,
                engine.STATUS_OVERSIZE,
            }
            for row in rows
        )
        ordered_results = sorted(
            rows, key=lambda row: row.get("request_started_ns", row.get("receipt_ns", 0))
        )
        consecutive_transients = 0
        for row in reversed(ordered_results):
            if row.get("status") in TRANSIENT_TERMINAL_STATUSES:
                consecutive_transients += 1
            else:
                break
        if consecutive_transients >= 3:
            manual = True
        terminal = max(cooldowns, key=lambda row: row["receipt_ns"], default=None)
        return {
            **summary,
            "cooldown_until_ns": terminal.get("cooldown_until_ns") if terminal else None,
            "manual_review_required": manual,
        }

    def append_dispatch_record(self, record: dict[str, Any]) -> None:
        self.guard.revalidate()
        self.writer.append(self._path("dispatch.jsonl"), canonical_json(record))

    def read_tick_monitor(self) -> list[dict[str, Any]]:
        path = self._path("tick-monitor.jsonl")
        if not self.writer.exists(path):
            return []
        raw = self.writer.read_bytes(path, max_bytes=2 * 1024 * 1024)
        if raw and not raw.endswith(b"\n"):
            raise GeckoRuntimeError("tick_monitor_partial")
        records = [json.loads(line, object_pairs_hook=_unique_object) for line in raw.splitlines()]
        previous = None
        for index, record in enumerate(records):
            core = dict(record)
            claimed = core.pop("record_sha256", None)
            if (
                set(record)
                != {
                    "schema",
                    "sequence",
                    "at_ns",
                    "status",
                    "reason",
                    "request_count",
                    "root_id",
                    "volume_uuid",
                    "free_bytes",
                    "previous_sha256",
                    "record_sha256",
                }
                or record["schema"] != "brain-senses-gecko-v3-tick-monitor"
                or record["sequence"] != index
                or record["previous_sha256"] != previous
                or not _is_sha(claimed)
                or claimed != sha256(canonical_json(core))
            ):
                raise GeckoRuntimeError("tick_monitor_chain_invalid")
            previous = claimed
        return records

    def append_tick_monitor(
        self, *, at_ns: int, status: str, reason: str, request_count: int, free_bytes: int
    ) -> None:
        rows = self.read_tick_monitor()
        record: dict[str, Any] = {
            "schema": "brain-senses-gecko-v3-tick-monitor",
            "sequence": len(rows),
            "at_ns": at_ns,
            "status": status,
            "reason": reason,
            "request_count": request_count,
            "root_id": self.guard.root_id,
            "volume_uuid": self.guard.volume_uuid.lower(),
            "free_bytes": free_bytes,
            "previous_sha256": rows[-1]["record_sha256"] if rows else None,
        }
        record["record_sha256"] = sha256(canonical_json(record))
        self.guard.revalidate()
        self.writer.append(self._path("tick-monitor.jsonl"), canonical_json(record))

    def clock_binding(
        self, bucket_close_ns: int, *, now_ns: Callable[[], int]
    ) -> tuple[str, str, dict[str, Any]]:
        prefix = f"{bucket_close_ns}-"
        candidates = sorted(name for name in self.files("clocks") if name.startswith(prefix))
        if candidates:
            latest = candidates[-1]
            relative = f"clocks/{latest}"
            raw = self.writer.read_bytes(self._path(relative), max_bytes=64 * 1024)
            record = self.read_json(relative)
            try:
                clock.validate_clock(record, now_ns=now_ns())
                return self._path(relative), sha256(raw), record
            except clock.ClockError:
                pass
        record = clock.probe_pair(wall=now_ns)
        clock.validate_clock(record, now_ns=now_ns())
        next_index = len(candidates)
        name = f"{bucket_close_ns}-{next_index:02d}.json"
        raw = canonical_json(record)
        self.guard.revalidate()
        self.writer.create(self._path(f"clocks/{name}"), raw)
        return self._path(f"clocks/{name}"), sha256(raw), record

    def seen_request(self, request_id: str) -> bool:
        filename = f"{request_id}.json"
        return filename in self.files("intents") or filename in self.files("results")

    def append_skip(self, skip: dict[str, Any]) -> None:
        path = self._path(f"skips/{skip['bucket_close_ns']}-{skip['pool_id']}.json")
        self.guard.revalidate()
        if self.writer.exists(path):
            raw = self.writer.read_bytes(path, max_bytes=64 * 1024)
            try:
                existing = json.loads(raw, object_pairs_hook=_unique_object)
                expected_fields = {
                    "schema",
                    "pool_id",
                    "bucket_close_ns",
                    "at_ns",
                    "reason",
                    "cooldown_until_ns",
                }
                if canonical_json(existing) != raw or set(existing) != expected_fields:
                    raise ValueError("skip_shape")
                engine.make_skip_record(
                    existing["pool_id"],
                    existing["bucket_close_ns"],
                    existing["at_ns"],
                    existing["reason"],
                    existing["cooldown_until_ns"],
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise GeckoRuntimeError("skip_record_conflict") from exc
            if any(
                existing.get(key) != skip.get(key)
                for key in ("schema", "pool_id", "bucket_close_ns", "reason", "cooldown_until_ns")
            ):
                raise GeckoRuntimeError("skip_record_conflict")
            return
        self.writer.create(path, canonical_json(skip))

    def append_stopped_missing(
        self, pool_id: str, bucket_close_ns: int, at_ns: int, reason: str
    ) -> None:
        if reason not in {"lane_stop", "spent_unresolved"}:
            raise GeckoRuntimeError("missing_pool_reason_invalid")
        record: dict[str, Any] = {
            "schema": "brain-senses-gecko-missing-pool-v3",
            "pool_id": pool_id,
            "bucket_close_ns": bucket_close_ns,
            "at_ns": at_ns,
            "reason": reason,
            "observed_as_zero": False,
        }
        record["record_sha256"] = sha256(canonical_json(record))
        path = self._path(f"skips/{bucket_close_ns}-{pool_id}.json")
        payload = canonical_json(record)
        self.guard.revalidate()
        if self.writer.exists(path):
            existing_raw = self.writer.read_bytes(path)
            existing = self.read_json(path.removeprefix(f"{self.root}/"))
            core = dict(existing)
            claimed = core.pop("record_sha256", None)
            if (
                existing.get("schema") != record["schema"]
                or existing.get("pool_id") != pool_id
                or existing.get("bucket_close_ns") != bucket_close_ns
                or existing.get("reason") != reason
                or existing.get("observed_as_zero") is not False
                or not _is_sha(claimed)
                or claimed != sha256(canonical_json(core))
                or not existing_raw.endswith(b"\n")
            ):
                raise GeckoRuntimeError("missing_pool_record_conflict")
            return
        self.writer.create(path, payload)

    def persist_response_and_result(
        self, terminal: dict[str, Any], raw: bytes | None, now_ns: Callable[[], int]
    ) -> bytes:
        request_id = terminal["request_id"]
        if raw is not None:
            self.guard.revalidate()
            self.writer.create(self._path(f"captures/{request_id}.bin"), raw)
        self.guard.revalidate()
        terminal["availability_ns"] = now_ns() if raw is not None else None
        terminal["durability"] = (
            "response_and_parent_fsynced" if raw is not None else "no_response_body"
        )
        payload = canonical_json(terminal)
        return payload


def run_current_bucket(
    plan: Mapping[str, Any],
    activation: Mapping[str, Any],
    store: DescriptorLaneStore,
    coordinator: Any,
    *,
    now_ns: Callable[[], int],
    wait_until: Callable[[int], None],
    fetch_once: Callable[[dict[str, Any], int, int], engine.FetchResult],
    clock_record: dict[str, Any] | None = None,
    clock_path: str | None = None,
    clock_sha256: str | None = None,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Serialize a whole scheduled tick using a v3-local descriptor lock."""
    try:
        with store.writer.lock(f"{store.root}/.runner.lock", blocking=False):
            return _run_current_bucket_locked(
                plan,
                activation,
                store,
                coordinator,
                now_ns=now_ns,
                wait_until=wait_until,
                fetch_once=fetch_once,
                clock_record=clock_record,
                clock_path=clock_path,
                clock_sha256=clock_sha256,
                monotonic_ns=monotonic_ns,
            )
    except Exception as exc:
        if type(exc).__name__ == "StorageError" and "active" in str(exc):
            raise GeckoRuntimeError("lane_tick_already_active") from exc
        raise


def _run_current_bucket_locked(
    plan: Mapping[str, Any],
    activation: Mapping[str, Any],
    store: DescriptorLaneStore,
    coordinator: Any,
    *,
    now_ns: Callable[[], int],
    wait_until: Callable[[int], None],
    fetch_once: Callable[[dict[str, Any], int, int], engine.FetchResult],
    clock_record: dict[str, Any] | None,
    clock_path: str | None,
    clock_sha256: str | None,
    monotonic_ns: Callable[[], int],
) -> dict[str, Any]:
    """Run only the current bucket through the validated host and coordinator gates."""
    verify_activation(plan, activation)
    store._verify_guard(plan)
    if (clock_record is None) != (clock_path is None) or (clock_record is None) != (
        clock_sha256 is None
    ):
        raise GeckoRuntimeError("clock_binding_incomplete")
    if clock_record is not None:
        clock.validate_clock(clock_record, now_ns=now_ns())
        if clock_path is None or not clock_path.startswith(f"{store.root}/clocks/"):
            raise GeckoRuntimeError("clock_path_invalid")
        raw_clock = store.writer.read_bytes(clock_path, max_bytes=64 * 1024)
        if sha256(raw_clock) != clock_sha256 or canonical_json(clock_record) != raw_clock:
            raise GeckoRuntimeError("clock_artifact_mismatch")
    # Lane-scoped recovery terminalizes only this service's orphaned reservations.
    coordinator.recover_service("gecko", intent_exists=lambda writer, path: writer.exists(path))
    store.reconcile_result_confirmations(now_ns)
    intent_ids = store.intent_request_ids()
    result_ids_before = {row["request_id"] for row in store.result_rows()}
    unresolved_ids = intent_ids - result_ids_before
    started = now_ns()
    bucket_close = current_bucket_for_tick(started)
    if bucket_close is None:
        return {"status": "outside_dispatch_window", "provider_io": False, "bucket_close_ns": None}

    if unresolved_ids:
        for request_id in sorted(unresolved_ids):
            intent = store.read_json(f"intents/{request_id}.json")
            request = intent["request"]
            store.append_stopped_missing(
                request["pool_id"], request["bucket_close_ns"], started, "spent_unresolved"
            )
        return {
            "status": "spent_unresolved_stop",
            "bucket_close_ns": bucket_close,
            "provider_io": False,
            "unresolved_request_ids": sorted(unresolved_ids),
        }

    policy = dict(plan["policy"])
    order = rotated_pool_order(policy, bucket_close)
    prior_state = store.dispatch_state()
    if prior_state is not None and prior_state["manual_review_required"]:
        for pool_id in order:
            request = engine.request_for_bucket(policy, pool_id, bucket_close)
            if not store.seen_request(request["request_id"]):
                store.append_stopped_missing(pool_id, bucket_close, started, "lane_stop")
        return {
            "status": "manual_review_stop",
            "bucket_close_ns": bucket_close,
            "provider_io": False,
            "stop_reason": "manual_review_required",
        }
    existing_results = store.result_rows()
    existing_intents = {
        request_id: store.read_json(f"intents/{request_id}.json")
        for request_id in store.intent_request_ids()
    }
    result_ids = {row.get("request_id") for row in existing_results}
    attempted: list[str] = [
        existing_intents[row["request_id"]]["request"]["pool_id"]
        for row in existing_results
        if row.get("request_id") in existing_intents
        and existing_intents[row["request_id"]]["request"]["bucket_close_ns"] == bucket_close
    ]
    terminal_statuses: list[str] = []
    # A prior bucket's final response must be durable at least 12 seconds before
    # the next request is eligible; this also prevents a restart burst.
    prior_confirmation_ns = store.latest_confirmation_ns()
    stop_reason: str | None = None
    for pool_id in order:
        request = engine.request_for_bucket(policy, pool_id, bucket_close)
        if store.seen_request(request["request_id"]):
            continue
        now = now_ns()
        start_ns, end_ns = engine.dispatch_window(bucket_close)
        if now < start_ns:
            wait_until(start_ns)
            now = now_ns()
        due_by = min(end_ns, bucket_close + MAX_REQUEST_START_OFFSET_NS)
        if prior_confirmation_ns is not None:
            next_eligible = prior_confirmation_ns + MINIMUM_DURABLE_RESULT_SPACING_NS
            if now < next_eligible:
                wait_until(next_eligible)
                now = now_ns()
        if now >= due_by:
            skip = engine.make_skip_record(
                pool_id, bucket_close, max(now, end_ns), "missed_dispatch"
            )
            store.append_skip(skip)
            continue
        reservation_holder: dict[str, capacity.Reservation] = {}

        def reserve(
            intent: dict[str, Any],
            reserve_bytes: int,
            _end_ns: int,
            holder: dict[str, capacity.Reservation] = reservation_holder,
        ) -> engine.ReservationResult:
            if clock_record is not None:
                intent["clock_path"] = clock_path
                intent["clock_sha256"] = clock_sha256
                intent["decision_monotonic_ns"] = monotonic_ns()
                intent_core = dict(intent)
                intent_core.pop("intent_sha256", None)
                intent["intent_sha256"] = sha256(canonical_json(intent_core))
            intent_path = f"{store.root}/intents/{intent['request_id']}.json"
            outcome_path = f"{store.root}/results/{intent['request_id']}.json"
            raw = canonical_json(intent)
            # execute_one_shot also owns lane serialization; the reservation API
            # requires its lease for the atomic shared-ledger write. Capture it
            # through the lane-specific adapter managed by this invocation.
            lease = getattr(coordinator, "_gecko_runtime_lease", None)
            if lease is None:
                raise GeckoRuntimeError("coordinator_lane_lease_required")
            reservation = coordinator.reserve_and_persist_intent(
                service="gecko",
                reservation_id=intent["request_id"],
                request_id=intent["request_id"],
                intent_path=intent_path,
                outcome_path=outcome_path,
                intent_bytes=raw,
                intent_sha256=sha256(raw),
                reserve_bytes=reserve_bytes,
                lane_lease=lease,
            )
            holder[intent["request_id"]] = reservation
            return engine.ReservationResult("reserved", now_ns())

        def append_dispatch(record: dict[str, Any]) -> None:
            store.append_dispatch_record(record)

        def state_gate(
            _request: dict[str, Any],
            checked_ns: int,
            request_deadline: int = due_by,
            request_end: int = end_ns,
            current_request_id: str = request["request_id"],
        ) -> bool:
            if clock_record is not None:
                try:
                    clock.validate_progress(
                        clock_record,
                        wall_ns=checked_ns,
                        monotonic_ns=monotonic_ns(),
                    )
                except clock.ClockError as exc:
                    raise GeckoRuntimeError("clock_progress_invalid") from exc
            if checked_ns >= request_deadline or request_end - checked_ns < 25 * offline.NS:
                return False
            if not _within_lane_budget(store, checked_ns, current_request_id):
                return False
            records = store.read_dispatch_records()
            recent = [
                row
                for row in records
                if row["request_id"] != current_request_id
                and checked_ns - row["dispatch_ns"] < 60 * offline.NS
            ]
            if len(recent) >= MAX_LANE_DISPATCHES_PER_60_SECONDS:
                return False
            confirmed_ns = store.latest_confirmation_ns()
            return (
                confirmed_ns is None
                or checked_ns - confirmed_ns >= MINIMUM_DURABLE_RESULT_SPACING_NS
            )

        def persist_terminal(
            terminal: dict[str, Any],
            raw: bytes | None,
            holder: dict[str, capacity.Reservation] = reservation_holder,
        ) -> None:
            reservation = holder.get(terminal["request_id"])
            if reservation is None:
                raise GeckoRuntimeError("terminal_without_reservation")
            terminal["bars"] = _runtime_bars(terminal.get("bars", []))
            payload = store.persist_response_and_result(terminal, raw, now_ns)
            status = terminal["status"]
            # Live v3 advances after an individual transient provider/transport
            # failure; offline-v3 cooldown behavior remains frozen and untouched.
            if status in TRANSIENT_TERMINAL_STATUSES:
                terminal["cooldown_until_ns"] = None
                terminal["cooldown_reason"] = None
                payload = canonical_json(terminal)
            coordinator.settle(
                reservation,
                outcome=status,
                outcome_bytes=payload,
                outcome_path=reservation.outcome_path,
                ambiguous=status == engine.STATUS_TRANSPORT,
                lane_lease=coordinator._gecko_runtime_lease,
            )
            store.append_result_confirmation(terminal["request_id"], sha256(payload), now_ns())

        def persist_skip(skip: dict[str, Any]) -> None:
            store.append_skip(skip)

        from contextlib import contextmanager

        @contextmanager
        def held_lane() -> Iterator[Any]:
            with coordinator.lane_lock("gecko") as lease:
                old = getattr(coordinator, "_gecko_runtime_lease", None)
                coordinator._gecko_runtime_lease = lease
                try:
                    yield lease
                finally:
                    coordinator._gecko_runtime_lease = old

        result = engine.execute_one_shot(
            policy,
            request,
            decision_ns=now,
            clock_ns=now_ns,
            lane_lock=held_lane,
            read_dispatch_state=store.dispatch_state,
            append_dispatch_record=append_dispatch,
            validate_dispatch_state=state_gate,
            reserve_intent=reserve,
            fetch_once=fetch_once,
            persist_terminal=persist_terminal,
            persist_skip=persist_skip,
        )
        if result.get("provider_io") is False:
            stop_reason = result.get("status", "not_dispatched")
            break
        attempted.append(pool_id)
        terminal_statuses.append(result["status"])
        prior_confirmation_ns = store.latest_confirmation_ns()
        if result["status"] in IMMEDIATE_STOP_STATUSES:
            stop_reason = result["status"]
            break
        if result["status"] in TRANSIENT_TERMINAL_STATUSES:
            recent_transients = 0
            for row in sorted(
                store.result_rows(),
                key=lambda item: item.get("request_started_ns", 0),
                reverse=True,
            ):
                if row.get("status") in TRANSIENT_TERMINAL_STATUSES:
                    recent_transients += 1
                else:
                    break
            if recent_transients >= 3:
                stop_reason = "three_consecutive_transient_failures"
                break
    now = now_ns()
    missing_pool_ids: list[str] = []
    result_ids = {row.get("request_id") for row in store.result_rows()}
    for pool_id in order:
        request = engine.request_for_bucket(policy, pool_id, bucket_close)
        has_result = request["request_id"] in result_ids
        if has_result:
            continue
        missing_pool_ids.append(pool_id)
        if store.seen_request(request["request_id"]):
            store.append_stopped_missing(pool_id, bucket_close, now, "spent_unresolved")
            continue
        if now >= engine.dispatch_window(bucket_close)[1]:
            try:
                store.append_skip(
                    engine.make_skip_record(pool_id, bucket_close, now, "missed_dispatch")
                )
            except Exception:
                # A prior skip may already exist after a crash; never replace it.
                pass
        elif stop_reason is not None:
            store.append_stopped_missing(pool_id, bucket_close, now, "lane_stop")
    return {
        "status": "bucket_incomplete" if stop_reason else "bucket_processed",
        "bucket_close_ns": bucket_close,
        "attempted_pool_ids": attempted,
        "terminal_statuses": terminal_statuses,
        "stop_reason": stop_reason,
        "provider_io": bool(attempted),
        "missing_pool_count": len(missing_pool_ids),
        "missing_pool_ids": missing_pool_ids,
    }


def verify_live_store(
    store: DescriptorLaneStore,
    plan: Mapping[str, Any],
    *,
    require_clock: bool = False,
) -> dict[str, Any]:
    """Independently replay request/result/capture bindings for the v3 journal prefix."""
    verify_live_plan(plan)
    store._verify_guard(plan)
    intents: dict[str, dict[str, Any]] = {}
    for name in store.files("intents"):
        if not name.endswith(".json"):
            raise GeckoRuntimeError("intent_inventory_unexpected")
        intent = store.read_json(f"intents/{name}")
        expected_fields = {
            "schema",
            "request_id",
            "request",
            "decision_ns",
            "provider_charge_units",
            "provider_charge_status",
            "intent_sha256",
        }
        clock_fields = {"clock_path", "clock_sha256", "decision_monotonic_ns"}
        if frozenset(intent) not in {
            frozenset(expected_fields),
            frozenset(expected_fields | clock_fields),
        }:
            raise GeckoRuntimeError("intent_shape_invalid")
        core = dict(intent)
        claimed = core.pop("intent_sha256")
        if (
            intent["schema"] != "brain-senses-gecko-intent-v3"
            or claimed != sha256(canonical_json(core))
            or intent["provider_charge_units"] is not None
            or intent["provider_charge_status"] != "unknown_unverified"
            or name != f"{intent['request_id']}.json"
        ):
            raise GeckoRuntimeError("intent_binding_invalid")
        request = intent["request"]
        if clock_fields <= set(intent):
            clock_path = intent["clock_path"]
            if not isinstance(clock_path, str) or not clock_path.startswith(
                f"{store.root}/clocks/"
            ):
                raise GeckoRuntimeError("intent_clock_path")
            raw_clock = store.writer.read_bytes(clock_path, max_bytes=64 * 1024)
            if sha256(raw_clock) != intent["clock_sha256"]:
                raise GeckoRuntimeError("intent_clock_hash")
            clock_record = json.loads(raw_clock, object_pairs_hook=_unique_object)
            try:
                clock.validate_progress(
                    clock_record,
                    wall_ns=intent["decision_ns"],
                    monotonic_ns=intent["decision_monotonic_ns"],
                )
            except clock.ClockError as exc:
                raise GeckoRuntimeError("intent_clock_progress") from exc
        elif require_clock:
            raise GeckoRuntimeError("intent_clock_missing")
        expected = engine.request_for_bucket(
            dict(plan["policy"]), request["pool_id"], request["bucket_close_ns"]
        )
        if request != expected or intent["request_id"] != expected["request_id"]:
            raise GeckoRuntimeError("intent_request_mismatch")
        intents[intent["request_id"]] = intent

    referenced_clocks: set[str] = set()
    intent_buckets = {row["request"]["bucket_close_ns"] for row in intents.values()}
    for name in store.files("clocks"):
        match = re.fullmatch(r"([0-9]+)-([0-9]{2})\.json", name)
        if match is None:
            raise GeckoRuntimeError("clock_inventory_unexpected")
        bucket_close_ns = int(match.group(1))
        if bucket_close_ns % (offline.BUCKET_SECONDS * offline.NS):
            raise GeckoRuntimeError("clock_inventory_bucket_invalid")
        relative = store._path(f"clocks/{name}")
        raw = store.writer.read_bytes(relative, max_bytes=64 * 1024)
        record = json.loads(raw, object_pairs_hook=_unique_object)
        if canonical_json(record) != raw:
            raise GeckoRuntimeError("clock_record_not_canonical")
        try:
            clock.validate_clock(record)
        except clock.ClockError as exc:
            raise GeckoRuntimeError("clock_record_invalid") from exc
        if bucket_close_ns in intent_buckets:
            if relative not in {row.get("clock_path") for row in intents.values()}:
                raise GeckoRuntimeError("unreferenced_clock_for_spent_bucket")
        referenced_clocks.add(relative)

    records = store.read_dispatch_records()
    record_by_id = {row["request_id"]: row for row in records}
    if len(record_by_id) != len(records):
        raise GeckoRuntimeError("dispatch_request_duplicate")
    results: dict[str, dict[str, Any]] = {}
    confirmation_rows = store.read_confirmation_records()
    confirmation_by_id = {row["request_id"]: row for row in confirmation_rows}
    if len(confirmation_by_id) != len(confirmation_rows):
        raise GeckoRuntimeError("confirmation_request_duplicate")
    for name in store.files("results"):
        if not name.endswith(".json"):
            raise GeckoRuntimeError("result_inventory_unexpected")
        result = store.read_json(f"results/{name}")
        request_id = result.get("request_id")
        if (
            name != f"{request_id}.json"
            or request_id not in intents
            or request_id in results
            or result.get("intent_sha256") != intents[request_id]["intent_sha256"]
            or type(result.get("receipt_ns")) is not int
            or type(result.get("request_started_ns")) is not int
        ):
            raise GeckoRuntimeError("result_binding_invalid")
        dispatch = record_by_id.get(request_id)
        if (
            dispatch is not None
            and result.get("dispatch_record_sha256") != dispatch["record_sha256"]
        ):
            raise GeckoRuntimeError("result_dispatch_binding")
        no_dispatch_status = result.get("status") in {
            engine.STATUS_NOT_DISPATCHED,
            engine.STATUS_NOT_DISPATCHED_SPACING,
            engine.STATUS_NOT_DISPATCHED_STATE,
            engine.STATUS_NOT_DISPATCHED_DEADLINE,
        }
        if (dispatch is None) != no_dispatch_status:
            raise GeckoRuntimeError("result_dispatch_presence")
        if dispatch is not None:
            close_ns = intents[request_id]["request"]["bucket_close_ns"]
            window_start, window_end = engine.dispatch_window(close_ns)
            if (
                result.get("dispatch_ns") != dispatch["dispatch_ns"]
                or dispatch["dispatch_ns"] < window_start
                or dispatch["dispatch_ns"] > close_ns + MAX_REQUEST_START_OFFSET_NS
                or window_end - dispatch["dispatch_ns"] < 25 * offline.NS
                or result.get("request_started_ns") < dispatch["dispatch_ns"]
                or dispatch["dispatch_ns"] < intents[request_id]["decision_ns"]
            ):
                raise GeckoRuntimeError("dispatch_window_binding")
        if result.get("response_sha256") is None:
            if result.get("availability_ns") is not None:
                raise GeckoRuntimeError("availability_without_capture")
        else:
            capture_path = store._path(f"captures/{request_id}.bin")
            raw = store.writer.read_bytes(capture_path, max_bytes=32 * 1024 * 1024)
            if (
                sha256(raw) != result["response_sha256"]
                or len(raw) != result["response_bytes"]
                or type(result.get("availability_ns")) is not int
                or result["availability_ns"] < result["receipt_ns"]
            ):
                raise GeckoRuntimeError("capture_binding_invalid")
        confirmation = confirmation_by_id.get(request_id)
        result_bytes = store.writer.read_bytes(store._path(f"results/{request_id}.json"))
        if (
            confirmation is None
            or confirmation["outcome_sha256"] != sha256(result_bytes)
            or confirmation["confirmed_ns"] < result["receipt_ns"]
            or (
                type(result.get("availability_ns")) is int
                and confirmation["confirmed_ns"] < result["availability_ns"]
            )
        ):
            raise GeckoRuntimeError("result_confirmation_binding")
        if result.get("status") in {engine.STATUS_RECEIVED, engine.STATUS_EMPTY}:
            request = intents[request_id]["request"]
            body = store.writer.read_bytes(
                store._path(f"captures/{request_id}.bin"), max_bytes=32 * 1024 * 1024
            )
            parsed_bars = _runtime_bars(
                engine.parse_response(body, request["bucket_close_ns"], result["receipt_ns"])
            )
            if parsed_bars != result.get("bars"):
                raise GeckoRuntimeError("bar_raw_reparse_binding")
        for bar in result.get("bars", []):
            if (
                bar.get("provider_receipt_ns") != result["receipt_ns"]
                or bar.get("provider_receipt_semantics")
                != "transport_receipt_only_not_decision_eligibility"
                or "source_available_ns" in bar
            ):
                raise GeckoRuntimeError("bar_receipt_time_binding")
        results[request_id] = result

    expected_captures = {
        f"{request_id}.bin"
        for request_id, result in results.items()
        if result.get("response_sha256") is not None
    }
    if set(store.files("captures")) != expected_captures:
        raise GeckoRuntimeError("capture_file_closure")
    if set(store.files("results")) != {f"{request_id}.json" for request_id in results}:
        raise GeckoRuntimeError("result_file_closure")
    if set(store.files("intents")) != {f"{request_id}.json" for request_id in intents}:
        raise GeckoRuntimeError("intent_file_closure")

    policy_pool_ids = {row["pool_id"] for row in plan["policy"]["pools"]}
    for name in store.files("skips"):
        if not name.endswith(".json"):
            raise GeckoRuntimeError("skip_inventory_unexpected")
        skip = store.read_json(f"skips/{name}")
        if (
            skip.get("pool_id") not in policy_pool_ids
            or type(skip.get("bucket_close_ns")) is not int
        ):
            raise GeckoRuntimeError("skip_identity_invalid")
        if name != f"{skip['bucket_close_ns']}-{skip['pool_id']}.json":
            raise GeckoRuntimeError("skip_path_binding")
        if skip.get("schema") == "brain-senses-gecko-skip-v3":
            if set(skip) != {
                "schema",
                "pool_id",
                "bucket_close_ns",
                "at_ns",
                "reason",
                "cooldown_until_ns",
            }:
                raise GeckoRuntimeError("skip_shape_invalid")
            try:
                engine.make_skip_record(
                    skip["pool_id"],
                    skip["bucket_close_ns"],
                    skip["at_ns"],
                    skip["reason"],
                    skip["cooldown_until_ns"],
                )
            except (TypeError, ValueError) as exc:
                raise GeckoRuntimeError("skip_semantics_invalid") from exc
        elif skip.get("schema") == "brain-senses-gecko-missing-pool-v3":
            core = dict(skip)
            claimed = core.pop("record_sha256", None)
            if (
                set(skip)
                != {
                    "schema",
                    "pool_id",
                    "bucket_close_ns",
                    "at_ns",
                    "reason",
                    "observed_as_zero",
                    "record_sha256",
                }
                or type(skip.get("at_ns")) is not int
                or skip.get("reason") not in {"lane_stop", "spent_unresolved"}
                or skip.get("observed_as_zero") is not False
                or not _is_sha(claimed)
                or claimed != sha256(canonical_json(core))
            ):
                raise GeckoRuntimeError("missing_pool_record_invalid")
        else:
            raise GeckoRuntimeError("skip_schema_invalid")

    if any(request_id not in intents for request_id in record_by_id):
        raise GeckoRuntimeError("dispatch_without_intent")
    if set(confirmation_by_id) != set(results):
        raise GeckoRuntimeError("orphan_confirmation")
    dispatches = sorted(records, key=lambda row: row["dispatch_ns"])
    dispatched_requests = [row["request_id"] for row in dispatches]
    if len(dispatched_requests) != len(set(dispatched_requests)):
        raise GeckoRuntimeError("dispatch_request_duplicate")
    per_bucket: dict[int, list[tuple[int, str]]] = {}
    for row in dispatches:
        intent = intents[row["request_id"]]
        request = intent["request"]
        ordinal = rotated_pool_order(dict(plan["policy"]), request["bucket_close_ns"]).index(
            request["pool_id"]
        )
        per_bucket.setdefault(request["bucket_close_ns"], []).append((ordinal, row["request_id"]))
    for bucket_rows in per_bucket.values():
        ordinals = [ordinal for ordinal, _request_id in bucket_rows]
        if ordinals != sorted(set(ordinals)):
            raise GeckoRuntimeError("pool_dispatch_order")
    for prior, current in zip(dispatches, dispatches[1:], strict=False):
        confirmation = confirmation_by_id.get(prior["request_id"])
        if (
            confirmation is None
            or current["dispatch_ns"] - confirmation["confirmed_ns"]
            < MINIMUM_DURABLE_RESULT_SPACING_NS
        ):
            raise GeckoRuntimeError("dispatch_before_durable_spacing")
    for index, row in enumerate(dispatches):
        rolling = sum(
            other["dispatch_ns"] > row["dispatch_ns"] - 60 * offline.NS
            and other["dispatch_ns"] <= row["dispatch_ns"]
            for other in dispatches
        )
        if rolling > MAX_LANE_DISPATCHES_PER_60_SECONDS:
            raise GeckoRuntimeError("rolling_rate_cap_exceeded")
        if index and row["dispatch_ns"] < dispatches[index - 1]["dispatch_ns"]:
            raise GeckoRuntimeError("dispatch_order_regression")
    return {
        "status": "verified_prefix",
        "intents": len(intents),
        "dispatches": len(records),
        "durable_results": len(results),
        "possibly_spent": len(intents) - len(results),
        "provider_io": False,
        "economic_admission": False,
        "event_time_semantics": "bar_open_event_time_only",
        "provider_receipt_semantics": "local_http_receipt_ns_not_decision_eligibility",
        "availability_semantics": "local_raw_capture_and_parent_fsync_completed",
        "eligibility_semantics": "confirmation_confirmed_ns_after_result_settlement_fsync",
        "clock_verified": all(
            {"clock_path", "clock_sha256", "decision_monotonic_ns"} <= set(row)
            for row in intents.values()
        ),
        "clock_artifacts": len(referenced_clocks),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _is_pilot_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _pilot_root(pilot_id: str) -> str:
    if not _is_pilot_id(pilot_id):
        raise GeckoRuntimeError("pilot_identity_invalid")
    return f"{PILOT_ROOT_PREFIX}/{pilot_id}"


def _runtime_bars(parsed_bars: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep transport receipt time explicitly separate from eligibility time."""
    rows = []
    for source in parsed_bars:
        row = dict(source)
        receipt_ns = row.pop("source_available_ns", None)
        row["provider_receipt_ns"] = receipt_ns
        row["provider_receipt_semantics"] = "transport_receipt_only_not_decision_eligibility"
        rows.append(row)
    return rows


def _public_fetch_once(
    request: dict[str, Any], max_bytes: int, timeout_seconds: int
) -> engine.FetchResult:
    """Credential-free bounded GET; redirects are treated as transport failures."""
    url = request.get("url")
    parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    pool_id = request.get("pool_id")
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname != "api.geckoterminal.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not isinstance(pool_id, str)
        or re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", pool_id) is None
        or parsed.path != f"/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute"
        or parsed.fragment
        or type(max_bytes) is not int
        or max_bytes < 0
        or type(timeout_seconds) is not int
        or timeout_seconds <= 0
    ):
        raise GeckoRuntimeError("public_request_binding")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
        ) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    http_request = urllib.request.Request(
        request["url"], headers=dict(request["headers"]), method="GET"
    )
    try:
        response = opener.open(http_request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        try:
            return engine.FetchResult(exc.code, exc.read(max_bytes + 1), dict(exc.headers.items()))
        finally:
            exc.close()
    with response:
        return engine.FetchResult(
            response.status,
            response.read(max_bytes + 1),
            dict(response.headers.items()),
        )


def _verify_scheduled_release(
    plan: Mapping[str, Any],
    release: Mapping[str, Any],
    pilot_receipt_sha256: str,
    pilot_receipt: Mapping[str, Any],
    store: DescriptorLaneStore,
) -> None:
    expected = {
        "schema",
        "schema_version",
        "plan_sha256",
        "source_sha256",
        "pilot_receipt_sha256",
        "status",
    }
    if not isinstance(release, Mapping) or set(release) != expected:
        raise GeckoRuntimeError("scheduled_release_shape")
    if (
        release["schema"] != "brain-senses-gecko-live-v3-release"
        or release["schema_version"] != 3
        or release["plan_sha256"] != plan["plan_sha256"]
        or release["source_sha256"] != plan["source_sha256"]
        or release["pilot_receipt_sha256"] != pilot_receipt_sha256
        or release["status"] != "passed"
    ):
        raise GeckoRuntimeError("scheduled_release_binding")
    _verify_source_map_shape(plan["source_sha256"])
    qualification, prefix = verify_pilot_bucket(
        store, plan, pilot_receipt["bucket_close_ns"], require_clock=True
    )
    if (
        qualification["status"] != "passed"
        or pilot_receipt.get("summary", {}).get("pilot_outcome") != "review_candidate"
        or pilot_receipt.get("summary", {}).get("pilot_qualification") != qualification
        or prefix != pilot_receipt.get("prefix")
        or sha256(canonical_json(prefix)) != pilot_receipt.get("result_sha256")
    ):
        raise GeckoRuntimeError("scheduled_pilot_qualification_failed")


def _compile_pinned_live_policy(writer: DescriptorWriter, guard: Any) -> dict[str, Any]:
    payloads = []
    for source_name in ("train_enrollment", "train_role_manifest"):
        source = offline.SOURCE_PATHS[source_name]
        prefix = "/Volumes/Kingston/BrainData/"
        if source.startswith(prefix):
            relative = source[len(prefix) :]
        elif not Path(source).is_absolute() and ".." not in Path(source).parts:
            relative = source
        else:
            raise GeckoRuntimeError("pinned_source_path_invalid")
        guard.revalidate()
        payloads.append(writer.read_bytes(relative, max_bytes=32 * 1024 * 1024))
        guard.revalidate()
    pinned = live_sources.verify_source_binding(
        writer,
        expected_root_id=guard.root_id,
        expected_volume_uuid=guard.volume_uuid,
    )
    guard.revalidate()
    return offline.compile_pinned_policy(
        enrollment_bytes=payloads[0],
        role_manifest_bytes=payloads[1],
        plan_bytes=pinned.plan_bytes,
        selection_bytes=pinned.selection_bytes,
    )


def run_host_mode(
    mode: str,
    *,
    pilot_id: str | None = None,
    now_ns: Callable[[], int] = time.time_ns,
) -> dict[str, Any]:
    """Use the single configured Kingston binding; never initializes absent artifacts."""
    if mode not in {
        "status",
        "preflight",
        "initialize-pilot",
        "pilot-once",
        "seal-pilot",
        "scheduled-tick",
        "import-source-pins",
    }:
        raise GeckoRuntimeError("host_mode_invalid")
    if mode != "import-source-pins" and not _is_pilot_id(pilot_id):
        raise GeckoRuntimeError("pilot_identity_required")
    with writer_guard(CONFIG_PATH) as guard:
        data_root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def revalidate_before_write() -> None:
            guard.revalidate()

        try:
            writer = DescriptorWriter(data_root_fd, before_write=revalidate_before_write)
        finally:
            os.close(data_root_fd)
        try:
            store: DescriptorLaneStore | None = None
            if mode != "import-source-pins":
                if not _is_pilot_id(pilot_id):
                    raise GeckoRuntimeError("pilot_identity_required")
                assert pilot_id is not None
                store = DescriptorLaneStore(writer, guard=guard, root=_pilot_root(pilot_id))
            bucket_close: int | None = None
            if mode == "import-source-pins":
                guard.revalidate()
                manifest = live_sources.import_source_pins(
                    writer,
                    root_id=guard.root_id,
                    volume_uuid=guard.volume_uuid,
                )
                guard.revalidate()
                return {
                    "status": "source_pins_imported",
                    "provider_io": False,
                    "manifest_sha256": sha256(canonical_json(manifest)),
                }
            if mode == "initialize-pilot":
                policy = _compile_pinned_live_policy(writer, guard)
                assert pilot_id is not None
                plan = compile_live_plan(
                    policy,
                    pilot_id=pilot_id,
                    root_id=guard.root_id,
                    volume_uuid=guard.volume_uuid,
                    source_map=_source_bindings_for_store(writer, guard),
                )
                current = now_ns()
                bucket_close = ((current // (900 * offline.NS)) + 1) * 900 * offline.NS
                authorization = {
                    "schema": PILOT_AUTH_SCHEMA,
                    "schema_version": 3,
                    "pilot_id": plan["pilot_id"],
                    "plan_sha256": plan["plan_sha256"],
                    "source_sha256": plan["source_sha256"],
                    "root_id": plan["storage"]["root_id"],
                    "volume_uuid": plan["storage"]["volume_uuid"],
                    "mode": "one_shot",
                    "bucket_close_ns": bucket_close,
                    "status": "approved",
                }
                assert store is not None
                store.initialize_pilot(plan, authorization, bucket_close)
                return {
                    "status": "pilot_initialized",
                    "provider_io": False,
                    "plan_sha256": plan["plan_sha256"],
                    "bucket_close_ns": bucket_close,
                }
            assert store is not None
            plan = store.read_json("plan.json")
            verify_live_plan(plan)
            store.bind_verified_plan(plan)
            store._verify_guard(plan)
            if plan["source_sha256"] != _source_bindings_for_store(writer, guard):
                raise GeckoRuntimeError("source_binding_changed")
            if mode == "status":
                return verify_live_store(store, plan, require_clock=True)

            if mode == "seal-pilot":
                authorization = store.read_json("pilot-authorization.json")
                authorized_close = authorization.get("bucket_close_ns")
                if type(authorized_close) is not int:
                    raise GeckoRuntimeError("pilot_authorization_binding")
                bucket_close = authorized_close
                verify_pilot_authorization(plan, authorization, bucket_close)
                summary = verify_live_store(store, plan, require_clock=True)
                qualification, prefix = verify_pilot_bucket(
                    store, plan, bucket_close, require_clock=True
                )
                if (
                    summary["intents"] != 4
                    or summary["dispatches"] != 4
                    or summary["durable_results"] != 4
                    or summary["possibly_spent"] != 0
                ):
                    qualification = {
                        **qualification,
                        "status": "diagnostic_only",
                        "reason": "pilot_lease_not_exactly_four_requests",
                    }
                bucket_results = [
                    row
                    for row in store.result_rows()
                    if store.read_json(f"intents/{row['request_id']}.json")["request"][
                        "bucket_close_ns"
                    ]
                    == bucket_close
                ]
                statuses = sorted(row["status"] for row in bucket_results)
                summary = {
                    **summary,
                    "bucket_close_ns": bucket_close,
                    "terminal_statuses": statuses,
                    "pilot_qualification": qualification,
                    "pilot_outcome": (
                        "review_candidate"
                        if qualification["status"] == "passed"
                        else "manual_rearm_required"
                    ),
                }
                receipt = {
                    "schema": PILOT_RECEIPT_SCHEMA,
                    "schema_version": 3,
                    "pilot_id": plan["pilot_id"],
                    "plan_sha256": plan["plan_sha256"],
                    "source_sha256": plan["source_sha256"],
                    "bucket_close_ns": bucket_close,
                    "verification_status": "passed",
                    "verifier_output_sha256": sha256(canonical_json(summary)),
                    "result_sha256": sha256(canonical_json(prefix)),
                    "summary": summary,
                    "prefix": prefix,
                }
                store._create_exact("pilot-receipt.json", receipt)
                return {
                    "status": "pilot_receipt_sealed",
                    "provider_io": False,
                    "receipt_sha256": sha256(canonical_json(receipt)),
                    "pilot_outcome": summary["pilot_outcome"],
                }

            coordinator = make_capacity_coordinator(writer, guard, now_ns=now_ns)
            if mode == "preflight":
                return {
                    "status": "preflight_passed",
                    "provider_io": False,
                    "plan_sha256": plan["plan_sha256"],
                }

            if mode == "pilot-once":
                pilot_bucket_close = current_bucket_for_tick(now_ns())
                if pilot_bucket_close is None:
                    return {
                        "status": "outside_dispatch_window",
                        "provider_io": False,
                        "bucket_close_ns": None,
                    }
                authorization = store.read_json("pilot-authorization.json")
                verify_pilot_authorization(plan, authorization, pilot_bucket_close)
                bucket_close = pilot_bucket_close
                activation = {
                    "schema": ACTIVATION_SCHEMA,
                    "schema_version": 3,
                    "pilot_id": plan["pilot_id"],
                    "plan_sha256": plan["plan_sha256"],
                    "source_sha256": plan["source_sha256"],
                    "root_id": plan["storage"]["root_id"],
                    "volume_uuid": plan["storage"]["volume_uuid"],
                    "activation_authorized": True,
                    "provider_io": True,
                    "qualification_status": "passed",
                }
            else:
                receipt_raw = writer.read_bytes(store._path("pilot-receipt.json"))
                receipt = store.read_json("pilot-receipt.json")
                receipt_sha = sha256(receipt_raw)
                verify_pilot_receipt(plan, receipt, receipt_sha)
                release = store.read_json("release.json")
                _verify_scheduled_release(plan, release, receipt_sha, receipt, store)
                activation = store.read_json("activation.json")
                verify_activation(plan, activation)

            active_bucket_close = current_bucket_for_tick(now_ns())
            if active_bucket_close is None:
                return {
                    "status": "outside_dispatch_window",
                    "provider_io": False,
                    "bucket_close_ns": None,
                }
            clock_path, clock_digest, clock_record = store.clock_binding(
                active_bucket_close, now_ns=now_ns
            )
            before_count = len(store.read_dispatch_records())
            try:
                result = run_current_bucket(
                    plan,
                    activation,
                    store,
                    coordinator,
                    now_ns=now_ns,
                    wait_until=lambda target: _wait_until(target, now_ns),
                    fetch_once=_public_fetch_once,
                    clock_record=clock_record,
                    clock_path=clock_path,
                    clock_sha256=clock_digest,
                    monotonic_ns=time.monotonic_ns,
                )
            except Exception as exc:
                try:
                    snapshot = guard.revalidate()
                    store.append_tick_monitor(
                        at_ns=now_ns(),
                        status="failed_closed",
                        reason=(
                            exc.args[0]
                            if isinstance(exc, GeckoRuntimeError)
                            else "host_gate_failure"
                        ),
                        request_count=len(store.read_dispatch_records()),
                        free_bytes=snapshot.available_bytes,
                    )
                except Exception:
                    pass
                raise
            snapshot = guard.revalidate()
            after_count = len(store.read_dispatch_records())
            store.append_tick_monitor(
                at_ns=now_ns(),
                status=result["status"],
                reason=str(result.get("stop_reason") or result["status"]),
                request_count=after_count,
                free_bytes=snapshot.available_bytes,
            )
            result["dispatches_this_tick"] = after_count - before_count
            return result
        finally:
            writer.close()


def _wait_until(target_ns: int, now_ns: Callable[[], int]) -> None:
    while True:
        remaining = target_ns - now_ns()
        if remaining <= 0:
            return
        time.sleep(min(remaining / offline.NS, 1.0))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--initialize-pilot", action="store_true")
    modes.add_argument("--pilot-once", action="store_true")
    modes.add_argument("--seal-pilot", action="store_true")
    modes.add_argument("--scheduled-tick", action="store_true")
    modes.add_argument("--import-source-pins", action="store_true")
    parser.add_argument("--pilot-id")
    args = parser.parse_args(argv)
    mode = (
        "status"
        if args.status
        else "preflight"
        if args.preflight
        else "initialize-pilot"
        if args.initialize_pilot
        else "pilot-once"
        if args.pilot_once
        else "seal-pilot"
        if args.seal_pilot
        else "import-source-pins"
        if args.import_source_pins
        else "scheduled-tick"
    )
    try:
        if mode != "import-source-pins" and not _is_pilot_id(args.pilot_id):
            raise GeckoRuntimeError("pilot_identity_required")
        result = run_host_mode(mode, pilot_id=args.pilot_id)
    except Exception as exc:
        error_class = type(exc).__name__
        reason = (
            exc.args[0]
            if isinstance(exc, GeckoRuntimeError)
            and exc.args
            and isinstance(exc.args[0], str)
            and re.fullmatch(r"[a-z0-9_]{1,64}", exc.args[0])
            else "host_gate_failure"
        )
        result = {
            "schema": "brain-senses-gecko-v3-runtime",
            "status": "failed_closed",
            "reason": reason,
            "error_class": error_class,
            "provider_io": False,
        }
    print(json.dumps(result, sort_keys=True))
    successful = result.get("status") != "failed_closed"
    if mode in {"pilot-once", "scheduled-tick"}:
        return (
            0
            if successful
            and result.get("status") in {"bucket_processed", "outside_dispatch_window"}
            else 2
        )
    return (
        0
        if successful
        and mode in {"status", "preflight", "initialize-pilot", "seal-pilot", "import-source-pins"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
