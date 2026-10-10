#!/usr/bin/env python3
"""Offline policy ledger for a finite, daily-rollover Helius Senses service.

This module deliberately owns no provider client or LaunchAgent integration. A reviewed
runner may reserve one bucket here, invoke the v3 epoch scheduler, then seal its reconciled
result. Reservations are durable before that boundary and are never replayed after ambiguity.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical, digest  # noqa: E402
from memecoin_ai.sources import Capture  # noqa: E402
from tools import brain_senses_block_epoch_v2 as epoch_v2  # noqa: E402
from tools import brain_senses_block_epoch_v3_schedule as schedule_v3  # noqa: E402

SCHEMA_VERSION = 2
SERVICE_DAYS = 7
DAILY_CAMPAIGNS_MAX = 7
BUCKETS_PER_DAY = 24
FRAME_REQUESTS = 34
RESET_REQUESTS = 1
INITIAL_BOOTSTRAP_REQUESTS = 1
DAILY_REQUESTS_MAX = 817
SERVICE_REQUESTS_MAX = 5_719
FRAME_RAW_BYTES = 256_000_000
FRAME_STORED_BYTES = 128_000_000
DAILY_RAW_BYTES = 4_000_000_000
DAILY_STORED_BYTES = 1_000_000_000
SERVICE_RAW_BYTES = 12_000_000_000  # provisional, preflight against observed tails
SERVICE_STORED_BYTES = 3_000_000_000  # provisional, preflight against observed tails
FRAME_WIDTH_MAX = 20_000
SELECTION_COUNT = 16
SELECTION_RULE = "uniform_without_replacement_from_finalized_getblocks_members"
DISK_FLOOR_BYTES = 50 * 1024**3
# c4 activation required 674,000,000 bytes above the 50 GiB floor for its
# full-frame peak envelope; retain that measured preflight margin.
DISK_PEAK_MARGIN_BYTES = 674_000_000
CODE_BINDINGS = (
    "tools/brain_senses_service_v1.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_epoch_v2_schedule.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_program_watch.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_campaign_controller.py",
    "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist",
    "src/memecoin_ai/sources.py",
    "src/memecoin_ai/domain.py",
)
TERMINAL_STOP_STATUSES = {"campaign_stopped", "service_stopped", "terminal"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _write(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    _atomic(path, (canonical(value) + "\n").encode(), exclusive=exclusive)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("service_record_not_object")
    return value


def _lock(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = (root / ".service-v1.lock").open("a")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ValueError("service_controller_already_active") from exc
    return handle


def _campaign_id(service_id: str, at_ns: int, generation: int) -> str:
    day = datetime.fromtimestamp(at_ns / 1e9, UTC).strftime("%Y%m%d")
    if not 1 <= generation <= DAILY_CAMPAIGNS_MAX:
        raise ValueError("campaign_generation_outside_bound")
    # Preserve the v3 campaign-id parser while still distinguishing each service
    # generation by UTC date and monotone daily sequence.
    return f"helius-inventory-v3-service-{service_id}-{day}-g{generation:02d}"


def _predecessor_proof(stop_path: Path, receipt_path: Path) -> dict[str, Any]:
    stop_bytes = stop_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()
    stop = json.loads(stop_bytes)
    receipt = json.loads(receipt_bytes)
    stop_hash = _sha(stop_bytes)
    receipt_hash = _sha(receipt_bytes)
    if (
        stop.get("campaign_id") != "helius-inventory-v3-20261010-4"
        or stop.get("reason") != "finite_attempt_cap_reached"
        or stop.get("attempt_count") != 24
        or stop.get("active_reservation_status") != "reconciled"
        or stop.get("active_bucket") is None
        or stop.get("accounting_error_class") is not None
        or not isinstance(stop.get("epoch_summary_sha256"), str)
        or len(stop["epoch_summary_sha256"]) != 64
        or not isinstance(stop.get("frame_reservation_sha256"), str)
        or len(stop["frame_reservation_sha256"]) != 64
        or type(stop.get("observed_anchor_slot")) is not int
        or type(stop.get("observed_frontier")) is not int
        or stop.get("observed_frontier") != stop.get("observed_anchor_slot") + 1
        or type(stop.get("observed_anchor_receipt_ns")) is not int
        or receipt.get("campaign_id") != stop.get("campaign_id")
        or receipt.get("status") != "normal_finite_terminal"
        or receipt.get("attempt_count") != stop.get("attempt_count")
        or receipt.get("active_reservation_status") != "reconciled"
        or receipt.get("accounting_error_count") != 0
        or receipt.get("stop_sha256") != stop_hash
        or receipt.get("post_stop_rpc_requests") != 0
    ):
        raise ValueError("predecessor_must_have_reconciled_finite_normal_stop")
    return {
        "stop_sha256": stop_hash,
        "terminal_receipt_sha256": receipt_hash,
        "committed_frontier_slot": stop["observed_anchor_slot"],
        "committed_frontier": stop["observed_frontier"],
        "committed_frontier_receipt_ns": stop["observed_anchor_receipt_ns"],
        "last_inventoried_interval": stop.get("last_inventoried_interval"),
        "gap_intervals": stop.get("gap_intervals", []),
    }


def _source_bindings() -> dict[str, str]:
    paths = set(CODE_BINDINGS) | set(schedule_v3._bindings())
    return {p: _sha((REPO_ROOT / p).read_bytes()) for p in sorted(paths)}


def _verify_sources(manifest: dict[str, Any]) -> None:
    if manifest.get("source_sha256") != _source_bindings():
        raise ValueError("service_source_hash_mismatch")


def _load_ledger(root: Path) -> list[dict[str, Any]]:
    path = root / "service-ledger.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous = "0" * 64
    reservations: set[str] = set()
    seals: set[str] = set()
    for seq, line in enumerate(path.read_text().splitlines()):
        row = json.loads(line)
        digest = row.pop("event_sha256", None)
        if row.get("seq") != seq or row.get("previous_sha256") != previous:
            raise ValueError("service_ledger_sequence_or_chain_invalid")
        expected = _sha(canonical(row).encode())
        if digest != expected:
            raise ValueError("service_ledger_hash_mismatch")
        kind = row.get("kind")
        if kind == "reservation":
            reservation_id = row.get("reservation_id")
            if not isinstance(reservation_id, str) or reservation_id in reservations:
                raise ValueError("service_ledger_duplicate_reservation")
            reservations.add(reservation_id)
        elif kind == "seal":
            reservation_id = row.get("reservation_id")
            if reservation_id not in reservations or reservation_id in seals:
                raise ValueError("service_ledger_orphan_or_duplicate_seal")
            seals.add(reservation_id)
        elif kind != "initialized" or seq != 0:
            raise ValueError("service_ledger_event_kind_invalid")
        row["event_sha256"] = digest
        events.append(row)
        previous = digest
    return events


def _append(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    events = _load_ledger(root)
    body = {
        **event,
        "seq": len(events),
        "previous_sha256": events[-1]["event_sha256"] if events else "0" * 64,
    }
    body["event_sha256"] = _sha(canonical(body).encode())
    path = root / "service-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("ab") as stream:
        stream.write((canonical(body) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    dfd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return body


def _usage(events: list[dict[str, Any]]) -> dict[str, int]:
    reservations = {e["reservation_id"]: e for e in events if e["kind"] == "reservation"}
    seals = {e["reservation_id"]: e for e in events if e["kind"] == "seal"}
    totals = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0, "outstanding": 0}
    for key, reservation in reservations.items():
        seal = seals.get(key)
        if seal is None:
            totals["requests"] += reservation["requests"]
            totals["raw_bytes"] += reservation["raw_bytes"]
            totals["stored_bytes"] += reservation["stored_bytes"]
            totals["outstanding"] += 1
        else:
            totals["requests"] += seal["actual_requests"]
            totals["raw_bytes"] += seal["actual_raw_bytes"]
            totals["stored_bytes"] += seal["actual_stored_bytes"]
    return totals


def _usage_for_day(events: list[dict[str, Any]], day: str) -> dict[str, int]:
    reservation_ids = {
        event["reservation_id"]
        for event in events
        if event.get("kind") == "reservation" and event.get("utc_date") == day
    }
    return _usage(
        [
            event
            for event in events
            if event.get("kind") != "reservation" or event.get("reservation_id") in reservation_ids
        ]
    )


def _fits_budget(
    usage: dict[str, int],
    *,
    requests: int,
    raw_bytes: int,
    stored_bytes: int,
    request_cap: int,
    raw_cap: int,
    stored_cap: int,
) -> bool:
    """Check additive reservations before any provider action."""
    return (
        usage["requests"] + requests <= request_cap
        and usage["raw_bytes"] + raw_bytes <= raw_cap
        and usage["stored_bytes"] + stored_bytes <= stored_cap
    )


def _stop(root: Path, reason: str, *, at_ns: int) -> dict[str, Any]:
    path = root / "STOP"
    if path.exists():
        return _read(path)
    record = {
        "schema_version": SCHEMA_VERSION,
        "status": "service_stopped",
        "reason": reason,
        "stopped_ns": at_ns,
    }
    _write(path, record, exclusive=True)
    return record


def _verify_campaigns(root: Path, controller: dict[str, Any]) -> None:
    service_hash = _sha((root / "service-manifest-v2.json").read_bytes())
    for campaign in controller.get("campaigns", []):
        day = campaign["utc_date"]
        generation = campaign["generation"]
        campaign_id = _campaign_id(
            controller["service_id"],
            int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC).timestamp() * 1e9),
            generation,
        )
        expected = {
            "schema_version": SCHEMA_VERSION,
            "campaign_id": campaign_id,
            "service_id": controller["service_id"],
            "utc_date": day,
            "generation": generation,
            "service_manifest_sha256": service_hash,
        }
        path = root / "campaigns" / campaign_id / "manifest-v2.json"
        if not path.is_file() or _read(path) != expected:
            raise ValueError("daily_campaign_manifest_changed")


def _verify_for_action(
    root: Path, manifest: dict[str, Any], controller: dict[str, Any], *, at_ns: int
) -> list[dict[str, Any]]:
    try:
        _verify_sources(manifest)
        _verify_campaigns(root, controller)
        events = _load_ledger(root)
        stop_events = [
            event
            for event in events
            if event.get("kind") == "seal"
            and event.get("outcome") in {"halt_unknown", "halt_review"}
        ]
        if stop_events:
            event = stop_events[-1]
            reason = (
                "provider_or_data_review_halt"
                if event["outcome"] == "halt_review"
                else "provider_or_data_review_halt"
            )
            _stop(root, reason, at_ns=event["at_ns"])
            raise ValueError("service_stopped")
        ambiguous_events = [
            event
            for event in events
            if event.get("kind") == "seal" and event.get("outcome") == "ambiguous"
        ]
        if len(ambiguous_events) > 1:
            _stop(root, "second_ambiguous_transport_circuit_breaker", at_ns=at_ns)
            raise ValueError("service_stopped")
        return events
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc) in {"service_source_hash_mismatch", "daily_campaign_manifest_changed"}
            else "local_journal_uncertain"
        )
        _stop(root, reason, at_ns=at_ns)
        raise ValueError(reason) from exc


def _recover_orphan_reservation(
    root: Path,
    *,
    controller: dict[str, Any],
    events: list[dict[str, Any]],
    at_ns: int,
) -> list[dict[str, Any]]:
    """Seal an append-before-writer crash only when absence of the network boundary is proven."""
    reservations = {e["reservation_id"]: e for e in events if e.get("kind") == "reservation"}
    sealed = {e["reservation_id"] for e in events if e.get("kind") == "seal"}
    outstanding = [row for key, row in reservations.items() if key not in sealed]
    if not outstanding:
        return events
    writer_path = root / "writer-active-v2.json"
    if writer_path.exists():
        writer = _read(writer_path)
        if (
            len(outstanding) != 1
            or writer.get("reservation_id") != outstanding[0]["reservation_id"]
        ):
            _stop(root, "service_writer_marker_uncertain", at_ns=at_ns)
            raise ValueError("service_writer_marker_uncertain")
        return events
    if len(outstanding) != 1:
        _stop(root, "orphan_reservation_count_uncertain", at_ns=at_ns)
        raise ValueError("orphan_reservation_count_uncertain")
    reservation = outstanding[0]
    campaign_root = root / "campaigns" / reservation["campaign_id"]
    bucket = reservation.get("plan", {}).get("bucket")
    if reservation.get("action") == "frame":
        epoch = campaign_root / "epochs" / str(bucket)
        activity = (
            list((epoch / "requests").glob("*.json")) if (epoch / "requests").is_dir() else []
        )
        activity += (
            list((epoch / "request-results").glob("*.json"))
            if (epoch / "request-results").is_dir()
            else []
        )
        active_path = campaign_root / "active-frame-reservation.json"
        active = _read(active_path) if active_path.is_file() else None
        unsafe = bool(activity) or bool(
            active and (active.get("status") != "reconciled" or active.get("bucket") == bucket)
        )
    else:
        epoch = campaign_root / "bootstrap-epochs" / str(bucket)
        unsafe = any(
            (epoch / name).exists() for name in ("epoch-plan.json", "STOP", "epoch-summary.json")
        ) or any(
            (epoch / name).is_dir() and any((epoch / name).iterdir())
            for name in ("requests", "request-results", "captures")
        )
    if unsafe:
        _stop(root, "orphan_reservation_has_epoch_activity", at_ns=at_ns)
        raise ValueError("orphan_reservation_has_epoch_activity")
    campaign = {
        "campaign_id": reservation["campaign_id"],
        "utc_date": reservation["utc_date"],
        "generation": int(reservation["campaign_id"].rsplit("g", 1)[1]),
    }
    if campaign not in controller.get("campaigns", []):
        controller["campaigns"] = [*controller.get("campaigns", []), campaign]
    controller["last_bucket_ns"] = max(
        int(controller["last_bucket_ns"] or 0), reservation["bucket_ns"]
    )
    controller["last_sealed_bucket_ns"] = reservation["bucket_ns"]
    if reservation.get("action") == "initial_bootstrap":
        controller["bootstrap_reserved"] = False
        controller["bootstrap_sealed"] = False
    else:
        controller["sleep_gap_pending"] = True
    seal = _append(
        root,
        {
            "kind": "seal",
            "reservation_id": reservation["reservation_id"],
            "campaign_id": reservation["campaign_id"],
            "outcome": "unstarted",
            "actual_requests": 0,
            "actual_raw_bytes": 0,
            "actual_stored_bytes": 0,
            "at_ns": at_ns,
            "evidence": {
                "bucket": bucket,
                "started_request_reservations": 0,
                "unstarted_request_capacity": reservation["requests"],
                "decision_time_ns": at_ns,
                "proof": ("reservation exists but writer marker and epoch activity are absent"),
            },
        },
    )
    _write(root / "controller-v2.json", controller)
    return [*events, seal]


def _reset_pending(events: list[dict[str, Any]]) -> bool:
    """Whether a first ambiguous transport still needs its one future-only reset."""
    reservations = {e["reservation_id"]: e for e in events if e.get("kind") == "reservation"}
    ambiguous = [e for e in events if e.get("kind") == "seal" and e.get("outcome") == "ambiguous"]
    if not ambiguous:
        return False
    last_ambiguous = ambiguous[-1]
    later_reset_success = any(
        event.get("kind") == "seal"
        and event.get("outcome") == "anchor_only"
        and reservations.get(event.get("reservation_id"), {}).get("action")
        in {"recovery_anchor", "sleep_gap_anchor"}
        and event.get("seq", -1) > last_ambiguous.get("seq", -1)
        for event in events
    )
    return not later_reset_success


def _recover_bootstrap(
    controller: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[bool, bool]:
    bootstrap_reservations = [
        event
        for event in events
        if event["kind"] == "reservation" and event.get("action") == "initial_bootstrap"
    ]
    reservation = bootstrap_reservations[-1] if bootstrap_reservations else None
    if reservation is None:
        return False, False
    seal = next(
        (
            event
            for event in events
            if event["kind"] == "seal" and event["reservation_id"] == reservation["reservation_id"]
        ),
        None,
    )
    controller["bootstrap_reserved"] = True
    if seal is None:
        return True, False
    if seal.get("outcome") == "unstarted":
        controller["bootstrap_reserved"] = False
        controller["bootstrap_sealed"] = False
        return False, False
    controller["bootstrap_sealed"] = seal["outcome"] == "bootstrap_complete"
    if controller["bootstrap_sealed"]:
        controller["frontier_slot"] = seal["evidence"]["next_start"] - 1
        controller["frontier_receipt_ns"] = seal["evidence"]["anchor_receipt_ns"]
    return True, controller["bootstrap_sealed"]


def _campaign_manifest(
    root: Path,
    *,
    campaign_id: str,
    service_id: str,
    day: str,
    generation: int,
    at_ns: int,
) -> None:
    path = root / "campaigns" / campaign_id / "manifest-v2.json"
    expected = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "service_id": service_id,
        "utc_date": day,
        "generation": generation,
        "service_manifest_sha256": _sha((root / "service-manifest-v2.json").read_bytes()),
    }
    if path.exists():
        if _read(path) != expected:
            _stop(root, "daily_campaign_manifest_changed", at_ns=at_ns)
            raise ValueError("daily_campaign_manifest_changed")
    else:
        _write(path, expected, exclusive=True)


def _allocate_campaign(
    root: Path,
    controller: dict[str, Any],
    *,
    day: str,
    at_ns: int,
    new_generation: bool,
) -> str:
    campaigns = list(controller.get("campaigns", []))
    on_day = [item for item in campaigns if item.get("utc_date") == day]
    if on_day and not new_generation:
        return on_day[-1]["campaign_id"]
    if len(campaigns) >= DAILY_CAMPAIGNS_MAX:
        _stop(root, "campaign_id_cap_reached", at_ns=at_ns)
        raise ValueError("campaign_id_cap_reached")
    generation = max((item["generation"] for item in on_day), default=0) + 1
    campaign_id = _campaign_id(controller["service_id"], at_ns, generation)
    _campaign_manifest(
        root,
        campaign_id=campaign_id,
        service_id=controller["service_id"],
        day=day,
        generation=generation,
        at_ns=at_ns,
    )
    campaigns.append({"campaign_id": campaign_id, "utc_date": day, "generation": generation})
    controller["campaigns"] = campaigns
    return campaign_id


def _bucket_plan(
    *,
    campaign_id: str,
    bucket: str,
    action: str,
    requests: int,
    raw: int,
    stored: int,
    manifest_sha256: str,
    next_start: int | None = None,
    predecessor_frontier_slot: int | None = None,
    predecessor_frontier_receipt_ns: int | None = None,
    predecessor_last_inventoried_interval: dict[str, Any] | None = None,
    predecessor_gap_intervals: list[dict[str, Any]] | None = None,
    previous_bucket_ns: int | None = None,
    anchor_only_gap_reason: str = "initial_bootstrap_frontier_established",
) -> dict[str, Any]:
    """Precommit v3-compatible deterministic sampling/cap identity before anchor I/O."""
    seed = _sha(f"{campaign_id}\0{bucket}".encode())
    return {
        "schema_version": SCHEMA_VERSION,
        "service_protocol": "helius-prospective-service-v1",
        "schedule_id": campaign_id,
        "bucket": bucket,
        "seed": seed,
        "action": action,
        "next_start": next_start,
        "predecessor_frontier_slot": predecessor_frontier_slot,
        "predecessor_frontier_receipt_ns": predecessor_frontier_receipt_ns,
        "predecessor_last_inventoried_interval": predecessor_last_inventoried_interval,
        "predecessor_gap_intervals": list(predecessor_gap_intervals or []),
        "previous_bucket_ns": previous_bucket_ns,
        "first_epoch_anchor_only": action != "frame",
        "anchor_only_gap_reason": anchor_only_gap_reason,
        "frame_width_cap": FRAME_WIDTH_MAX,
        "selection_count": SELECTION_COUNT,
        "selection_rule": SELECTION_RULE,
        "request_budget": requests,
        "raw_byte_budget": raw,
        "stored_byte_budget": stored,
        "minimum_free_disk_bytes": DISK_FLOOR_BYTES,
        "peak_write_margin_bytes": DISK_PEAK_MARGIN_BYTES,
        "capture_storage_codec": "zstd",
        "service_manifest_sha256": manifest_sha256,
        "epoch_source_sha256": schedule_v3._bindings(),
    }


def _campaign_tree_sha256(root: Path) -> str:
    hasher = hashlib.sha256()
    ignored_root_paths = {
        ".schedule-v3.lock",
        "rollover-close-intent-v1.json",
        "rollover-closure-v1.json",
        "STOP",
    }
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root).as_posix()
        if relative_path in ignored_root_paths:
            continue
        files.append((relative_path, path))
    for relative_path, path in sorted(files):
        relative = relative_path.encode()
        hasher.update(len(relative).to_bytes(4, "big"))
        hasher.update(relative)
        hasher.update(bytes.fromhex(_sha(path.read_bytes())))
    return hasher.hexdigest()


def _close_and_initialize_adjacent_rollover(
    root: Path,
    *,
    controller: dict[str, Any],
    events: list[dict[str, Any]],
    day: str,
    bucket_ns: int,
    prior_bucket_ns: int,
) -> str:
    """Close the prior v3 root and initialize a no-RPC lineage for an adjacent UTC day."""
    hour_ns = 3600 * 1_000_000_000
    campaigns = list(controller.get("campaigns", []))
    if not campaigns or bucket_ns != prior_bucket_ns + hour_ns:
        raise ValueError("adjacent_campaign_rollover_proof_missing")
    previous_campaign = campaigns[-1]
    if previous_campaign.get("utc_date") == day:
        raise ValueError("campaign_rollover_not_utc_day_boundary")
    previous_id = previous_campaign["campaign_id"]
    prior_bucket = datetime.fromtimestamp(prior_bucket_ns / 1e9, UTC).strftime("%Y%m%dT%H0000Z")
    reservations = [
        event
        for event in events
        if event.get("kind") == "reservation" and event.get("campaign_id") == previous_id
    ]
    if not reservations:
        raise ValueError("rollover_prior_reservation_missing")
    if _usage(events)["outstanding"]:
        raise ValueError("rollover_has_unreconciled_service_reservation")
    reservation = max(reservations, key=lambda event: event.get("bucket_ns", -1))
    if (
        reservation.get("bucket_ns") != prior_bucket_ns
        or reservation.get("plan", {}).get("bucket") != prior_bucket
    ):
        raise ValueError("rollover_not_adjacent_to_prior_bucket")
    campaign_root = root / "campaigns" / previous_id
    reservation_seals = {
        event.get("reservation_id"): event
        for event in events
        if event.get("kind") == "seal" and event.get("campaign_id") == previous_id
    }
    if len(reservation_seals) != len(reservations):
        raise ValueError("rollover_prior_epoch_unsealed")
    last_outcome = None
    last_seal: dict[str, Any] | None = None
    for prior_reservation in sorted(reservations, key=lambda event: event["bucket_ns"]):
        prior_seal = reservation_seals.get(prior_reservation["reservation_id"])
        if prior_reservation.get("action") == "frame":
            prior_outcome, prior_evidence, prior_summary = _verified_epoch_evidence(
                root, prior_reservation, historical=True
            )
            qualified = (
                prior_outcome == "complete" and prior_summary.get("status") == "frame_complete"
            )
        elif prior_seal.get("outcome") == "unstarted":
            epoch = (
                campaign_root
                / "bootstrap-epochs"
                / str(prior_reservation.get("plan", {}).get("bucket"))
            )
            if (
                prior_seal.get("actual_requests") != 0
                or prior_seal.get("actual_raw_bytes") != 0
                or prior_seal.get("actual_stored_bytes") != 0
                or any((epoch / name).exists() for name in ("epoch-plan.json", "STOP"))
            ):
                raise ValueError("rollover_unstarted_epoch_evidence_mismatch")
            prior_outcome, prior_evidence, prior_summary, qualified = "unstarted", {}, {}, True
        else:
            prior_outcome, prior_evidence, prior_summary, _ = _external_bootstrap_evidence(
                root, prior_reservation, controller
            )
            qualified = (
                prior_outcome in {"bootstrap_complete", "anchor_only"}
                and prior_summary.get("status") == "bootstrap_complete"
            )
        expected_requests = prior_evidence.get("request_reservations", 0)
        if (
            not qualified
            or prior_outcome != prior_seal.get("outcome")
            or prior_seal.get("actual_requests") != expected_requests
            or prior_seal.get("actual_raw_bytes") != prior_evidence.get("raw_response_bytes", 0)
            or prior_seal.get("actual_stored_bytes")
            != prior_evidence.get("stored_response_bytes", 0)
            or any(
                prior_evidence.get(key) != prior_seal.get("evidence", {}).get(key)
                for key in (
                    "epoch_plan_sha256",
                    "schedule_manifest_sha256",
                    "epoch_summary_sha256",
                    "summary_sha256",
                    "stop_sha256",
                    "lineage_sha256",
                )
                if key in prior_seal.get("evidence", {})
            )
        ):
            raise ValueError("rollover_prior_epoch_not_qualified")
        if prior_reservation["reservation_id"] == reservation["reservation_id"]:
            last_outcome = prior_outcome
            last_seal = prior_seal
    if last_outcome not in {"complete", "bootstrap_complete", "anchor_only"} or last_seal is None:
        raise ValueError("rollover_last_epoch_not_successful")
    expected_status = "frame_complete" if last_outcome == "complete" else "bootstrap_complete"

    lock_path = campaign_root / ".schedule-v3.lock"
    campaign_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_handle = lock_path.open("a")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("rollover_v3_writer_active") from exc
        if (campaign_root / "invocation-in-progress.json").exists():
            raise ValueError("rollover_v3_invocation_in_progress")
        active_path = campaign_root / "active-frame-reservation.json"
        if active_path.is_file():
            active = _read(active_path)
            if active.get("status") != "reconciled" or active.get("bucket") != prior_bucket:
                raise ValueError("rollover_v3_active_reservation_unreconciled")
        frontier_path = campaign_root / "frontier-v3.json"
        frontier = _read(frontier_path)
        if (
            frontier.get("last_attempt_bucket") != prior_bucket
            or frontier.get("last_terminal_status") not in {None, expected_status}
            or (
                expected_status == "frame_complete"
                and frontier.get("last_terminal_status") != expected_status
            )
            or frontier.get("observed_anchor_slot") != controller.get("frontier_slot")
            or frontier.get("observed_anchor_receipt_ns") != controller.get("frontier_receipt_ns")
            or frontier.get("observed_frontier") != controller.get("frontier_slot") + 1
        ):
            raise ValueError("rollover_frontier_or_anchor_mismatch")
        intent = {
            "schema_version": 1,
            "campaign_id": previous_id,
            "next_bucket_ns": bucket_ns,
            "prior_bucket": prior_bucket,
            "campaign_tree_sha256": _campaign_tree_sha256(campaign_root),
            "service_manifest_sha256": _sha((root / "service-manifest-v2.json").read_bytes()),
            "prior_service_seal_sha256": _sha(canonical(last_seal).encode()),
            "no_rpc": True,
        }
        intent_path = campaign_root / "rollover-close-intent-v1.json"
        if intent_path.exists():
            if _read(intent_path) != intent:
                raise ValueError("rollover_close_intent_changed")
        else:
            _write(intent_path, intent, exclusive=True)
        stop_path = campaign_root / "STOP"
        if stop_path.exists():
            stop = _read(stop_path)
            if stop.get("reason") not in {
                "service_utc_day_rollover_closed",
                "finite_attempt_cap_reached",
            }:
                raise ValueError("rollover_prior_campaign_already_stopped")
        else:
            stop = schedule_v3._seal_stop(
                campaign_root,
                "service_utc_day_rollover_closed",
                administrative_no_rpc=True,
                next_bucket_ns=bucket_ns,
                prior_service_seal_sha256=intent["prior_service_seal_sha256"],
            )
        closure = {
            "schema_version": 1,
            "campaign_id": previous_id,
            "next_bucket_ns": bucket_ns,
            "prior_bucket": prior_bucket,
            "campaign_tree_sha256": intent["campaign_tree_sha256"],
            "close_intent_sha256": _sha(intent_path.read_bytes()),
            "stop_sha256": _sha(stop_path.read_bytes()),
            "stop_reason": stop.get("reason"),
            "closed_at_ns": stop.get("time_ns"),
            "no_rpc": True,
            "old_root_permanently_non_writable": True,
        }
        closure_path = campaign_root / "rollover-closure-v1.json"
        if closure_path.exists():
            if _read(closure_path) != closure:
                raise ValueError("rollover_closure_changed")
        else:
            _write(closure_path, closure, exclusive=True)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    next_id = _allocate_campaign(root, controller, day=day, at_ns=bucket_ns, new_generation=False)
    next_root = root / "campaigns" / next_id
    anchor_slot = frontier["observed_anchor_slot"]
    anchor_receipt_ns = frontier["observed_anchor_receipt_ns"]
    if type(anchor_slot) is not int or type(anchor_receipt_ns) is not int:
        raise ValueError("rollover_anchor_receipt_unqualified")
    lineage = {
        "schema_version": 1,
        "provider": "helius-free",
        "campaign_id": next_id,
        "controller_lease_expires_ns": controller["expires_at_ns"],
        "last_observed_anchor_slot": anchor_slot,
        "last_observed_anchor_receipt_ns": anchor_receipt_ns,
        "last_attempt_bucket": prior_bucket,
        "last_inventoried_interval": frontier.get("last_inventoried_interval"),
        "gap_intervals": list(frontier.get("gap_intervals", [])),
        "rollover": {
            "schema_version": 1,
            "previous_campaign_id": previous_id,
            "previous_root_sha256": _campaign_tree_sha256(campaign_root),
            "previous_stop_sha256": closure["stop_sha256"],
            "previous_closure_sha256": _sha(closure_path.read_bytes()),
            "previous_bucket": prior_bucket,
            "no_rpc": True,
        },
        "service_manifest_sha256": _sha((root / "service-manifest-v2.json").read_bytes()),
        "schedule_source_sha256": schedule_v3._bindings(),
        "epoch_source_sha256": _sha(Path(epoch_v2.__file__).read_bytes()),
    }
    lineage_path = next_root / "predecessor-lineage-v1.json"
    if lineage_path.exists():
        if _read(lineage_path) != lineage:
            raise ValueError("rollover_predecessor_lineage_changed")
    else:
        _write(lineage_path, lineage, exclusive=True)
    schedule_v3.initialize(next_root, lineage_path)
    controller["campaign_handoff_pending"] = False
    _write(root / "controller-v2.json", controller)
    return next_id


def _external_bootstrap_plan(
    *, campaign_id: str, bucket: str, seed: str, service_manifest_sha256: str
) -> dict[str, Any]:
    """Pin a single-request v2 bootstrap independently of the frozen v3 scheduler."""
    return epoch_v2.build_epoch_plan(
        epoch_id=bucket,
        bucket=bucket,
        schedule_id=campaign_id,
        schedule_sha256=service_manifest_sha256,
        seed=seed,
        bootstrap=True,
        next_start=None,
        frame_width_cap=FRAME_WIDTH_MAX,
        selection_count=SELECTION_COUNT,
        request_budget=RESET_REQUESTS,
        raw_byte_budget=1_000_000,
        stored_byte_budget=1_000_000,
        minimum_free_disk_bytes=DISK_FLOOR_BYTES + DISK_PEAK_MARGIN_BYTES,
        capture_storage_codec="zstd",
        max_response_bytes=1_000_000,
    )


def _initial_controller(manifest: dict[str, Any]) -> dict[str, Any]:
    predecessor = manifest["predecessor_evidence"]
    return {
        "schema_version": SCHEMA_VERSION,
        "service_id": manifest["service_id"],
        "authorized_at_ns": manifest["authorized_at_ns"],
        "expires_at_ns": manifest["expires_at_ns"],
        "last_bucket_ns": None,
        "campaigns": [],
        "ambiguous_recovery_used": False,
        "bootstrap_reserved": False,
        "bootstrap_sealed": False,
        "frontier_slot": predecessor["committed_frontier_slot"],
        "frontier_receipt_ns": predecessor["committed_frontier_receipt_ns"],
        "last_inventoried_interval": predecessor["last_inventoried_interval"],
        "gap_intervals": list(predecessor["gap_intervals"]),
        "recovery_generation_pending": False,
        "sleep_gap_pending": False,
        "campaign_handoff_pending": False,
    }


def _initialize_marker(manifest: dict[str, Any]) -> dict[str, Any]:
    controller = _initial_controller(manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": _sha(canonical(manifest).encode()),
        "initial_controller_sha256": _sha(canonical(controller).encode()),
        "policy": "repair only manifest/controller/initialized-ledger prefix; no reservations",
    }


def initialize(
    root: Path,
    *,
    service_id: str,
    authorized_at_ns: int,
    expires_at_ns: int,
    predecessor_stop_path: Path,
    terminal_receipt_path: Path,
) -> dict[str, Any]:
    """Create a finite service manifest/controller from a verified terminal predecessor proof."""
    if not service_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in service_id
    ):
        raise ValueError("service_id_invalid")
    if (
        expires_at_ns <= authorized_at_ns
        or expires_at_ns > authorized_at_ns + SERVICE_DAYS * 86400 * 1_000_000_000
    ):
        raise ValueError("service_authorization_must_be_positive_and_at_most_7_days")
    predecessor_proof = _predecessor_proof(predecessor_stop_path, terminal_receipt_path)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = root / "service-manifest-v2.json"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "service_id": service_id,
        "provider": "helius-free",
        "authorized_at_ns": authorized_at_ns,
        "expires_at_ns": expires_at_ns,
        "max_days": SERVICE_DAYS,
        "max_daily_campaigns": DAILY_CAMPAIGNS_MAX,
        "max_daily_buckets": BUCKETS_PER_DAY,
        "max_daily_requests": DAILY_REQUESTS_MAX,
        "max_total_requests": SERVICE_REQUESTS_MAX,
        "max_daily_raw_bytes": DAILY_RAW_BYTES,
        "max_daily_stored_bytes": DAILY_STORED_BYTES,
        "max_total_raw_bytes_provisional": SERVICE_RAW_BYTES,
        "max_total_stored_bytes_provisional": SERVICE_STORED_BYTES,
        "frame": {
            "requests": FRAME_REQUESTS,
            "raw_bytes": FRAME_RAW_BYTES,
            "stored_bytes": FRAME_STORED_BYTES,
            "width_cap": FRAME_WIDTH_MAX,
            "selection_count": SELECTION_COUNT,
            "selection_rule": SELECTION_RULE,
            "capture_storage_codec": "zstd",
        },
        "anchor_only": {
            "requests": RESET_REQUESTS,
            "ambiguous_transport_recoveries_per_lease": 1,
            "sleep_gap_resets": "multiple within campaign/day/request/byte caps",
            "policy": "anchor-only at a strictly future scheduled bucket; never backfill",
        },
        "disk": {
            "free_floor_bytes": DISK_FLOOR_BYTES,
            "peak_margin_bytes": DISK_PEAK_MARGIN_BYTES,
            "physical_du_required": True,
        },
        "missed_bucket_policy": "record gap; next future tick is anchor-only; never backfill",
        "ambiguous_request_policy": (
            "count started requests spent, preserve committed frontier, one successor generation"
        ),
        "stop_policy": (
            "429/quota, malformed/inconsistent data, source/hash mismatch, "
            "disk/budget breach, or uncertain journal halt"
        ),
        "predecessor_evidence": predecessor_proof,
        "source_sha256": _source_bindings(),
        "credit_units": "unverified; request counts are not provider charge units",
    }
    handle = _lock(root)
    try:
        marker_path = root / "initialize-v2.json"
        expected_marker = _initialize_marker(manifest)
        if marker_path.exists():
            if _read(marker_path) != expected_marker:
                _stop(root, "service_initialization_marker_changed", at_ns=authorized_at_ns)
                raise ValueError("service_initialization_marker_changed")
        else:
            _write(marker_path, expected_marker, exclusive=True)
        if manifest_path.exists():
            if _read(manifest_path) != manifest:
                _stop(root, "service_manifest_binding_changed", at_ns=authorized_at_ns)
                raise ValueError("service_manifest_binding_changed")
        else:
            _write(manifest_path, manifest, exclusive=True)
        controller_path = root / "controller-v2.json"
        expected_controller = _initial_controller(manifest)
        if controller_path.exists():
            current_controller = _read(controller_path)
            if current_controller != expected_controller:
                # initialize() is only allowed to reconcile its no-reservation prefix.
                if not _load_ledger(root):
                    _stop(root, "service_initial_controller_changed", at_ns=authorized_at_ns)
                    raise ValueError("service_initial_controller_changed")
        else:
            events = _load_ledger(root)
            if events:
                _stop(
                    root, "service_controller_missing_after_ledger_activity", at_ns=authorized_at_ns
                )
                raise ValueError("service_controller_missing_after_ledger_activity")
            _write(controller_path, expected_controller, exclusive=True)
            current_controller = expected_controller
        events = _load_ledger(root)
        if not events:
            if current_controller != expected_controller:
                _stop(root, "service_ledger_missing_after_activity", at_ns=authorized_at_ns)
                raise ValueError("service_ledger_missing_after_activity")
            _append(
                root,
                {
                    "kind": "initialized",
                    "at_ns": authorized_at_ns,
                    "manifest_sha256": _sha(canonical(manifest).encode()),
                },
            )
        elif (
            events[0].get("kind") != "initialized"
            or events[0].get("manifest_sha256") != expected_marker["manifest_sha256"]
        ):
            _stop(root, "service_initial_ledger_binding_changed", at_ns=authorized_at_ns)
            raise ValueError("service_initial_ledger_binding_changed")
        if events:
            try:
                _verify_sources(manifest)
                _verify_campaigns(root, current_controller)
                events = _recover_orphan_reservation(
                    root,
                    controller=current_controller,
                    events=events,
                    at_ns=authorized_at_ns,
                )
                reservations = [event for event in events if event.get("kind") == "reservation"]
                if reservations:
                    expected_last_bucket = max(event["bucket_ns"] for event in reservations)
                    campaign_ids = {item["campaign_id"] for item in current_controller["campaigns"]}
                    if current_controller.get("last_bucket_ns") != expected_last_bucket or any(
                        event["campaign_id"] not in campaign_ids for event in reservations
                    ):
                        raise ValueError("service_controller_ledger_frontier_mismatch")
                elif current_controller != expected_controller:
                    raise ValueError("service_controller_without_reservations_mismatch")
            except Exception as exc:
                _stop(root, "service_initialization_reconciliation_failed", at_ns=authorized_at_ns)
                raise ValueError("service_initialization_reconciliation_failed") from exc
    finally:
        handle.close()
    return manifest


def reserve_bootstrap(
    root: Path, *, at_ns: int, free_disk_bytes: int | None = None
) -> dict[str, Any]:
    """Reserve the one initial anchor-only epoch before any provider I/O."""
    handle = _lock(root)
    try:
        controller = _read(root / "controller-v2.json")
        manifest = _read(root / "service-manifest-v2.json")
        if (root / "STOP").exists():
            raise ValueError("service_stopped")
        if at_ns >= controller["expires_at_ns"]:
            _stop(root, "service_lease_expired", at_ns=at_ns)
            raise ValueError("service_lease_expired")
        if at_ns < controller["authorized_at_ns"]:
            raise ValueError("service_not_yet_authorized")
        events = _verify_for_action(root, manifest, controller, at_ns=at_ns)
        events = _recover_orphan_reservation(
            root, controller=controller, events=events, at_ns=at_ns
        )
        bootstrap_found, bootstrap_sealed = _recover_bootstrap(controller, events)
        if bootstrap_found:
            _write(root / "controller-v2.json", controller)
            if not bootstrap_sealed:
                raise ValueError("bootstrap_requires_offline_reconciliation")
            raise ValueError("bootstrap_already_reserved_or_spent")
        hour_ns = 3600 * 1_000_000_000
        bucket_ns = at_ns - at_ns % hour_ns
        bucket = datetime.fromtimestamp(bucket_ns / 1e9, UTC).strftime("%Y%m%dT%H0000Z")
        if (root / "writer-active-v2.json").exists():
            raise ValueError("service_writer_pending_offline_reconciliation")
        previous_bucket_ns = controller.get("last_bucket_ns")
        if previous_bucket_ns is not None and bucket_ns <= previous_bucket_ns:
            raise ValueError("bucket_identity_already_spent_or_nonmonotonic")
        prior_reservations = [
            event
            for event in events
            if event.get("kind") == "reservation" and event.get("bucket_ns") == bucket_ns
        ]
        if prior_reservations:
            raise ValueError("bucket_identity_already_spent_or_reserved")
        day = datetime.fromtimestamp(bucket_ns / 1e9, UTC).strftime("%Y%m%d")
        day_bucket_count = sum(
            1
            for event in events
            if event.get("kind") == "reservation"
            and datetime.fromtimestamp(event["bucket_ns"] / 1e9, UTC).strftime("%Y%m%d") == day
        )
        if day_bucket_count >= BUCKETS_PER_DAY:
            _stop(root, "daily_bucket_cap_reached", at_ns=at_ns)
            raise ValueError("daily_bucket_cap_reached")
        usage = _usage(events)
        day_usage = _usage_for_day(events, day)
        if usage["outstanding"]:
            raise ValueError("prior_reservation_requires_offline_reconciliation")
        if not _fits_budget(
            usage,
            requests=1,
            raw_bytes=1_000_000,
            stored_bytes=1_000_000,
            request_cap=SERVICE_REQUESTS_MAX,
            raw_cap=SERVICE_RAW_BYTES,
            stored_cap=SERVICE_STORED_BYTES,
        ) or not _fits_budget(
            day_usage,
            requests=1,
            raw_bytes=1_000_000,
            stored_bytes=1_000_000,
            request_cap=DAILY_REQUESTS_MAX,
            raw_cap=DAILY_RAW_BYTES,
            stored_cap=DAILY_STORED_BYTES,
        ):
            _stop(root, "bootstrap_budget_exhausted", at_ns=at_ns)
            raise ValueError("bootstrap_budget_exhausted")
        physical_bytes = _physical_bytes(root / "campaigns")
        free_disk = shutil.disk_usage(root).free if free_disk_bytes is None else free_disk_bytes
        if (
            free_disk < DISK_FLOOR_BYTES + DISK_PEAK_MARGIN_BYTES
            or physical_bytes + 1_000_000 > SERVICE_STORED_BYTES
        ):
            _stop(root, "bootstrap_disk_or_physical_budget_breach", at_ns=at_ns)
            raise ValueError("bootstrap_preflight_failed")
        campaign_id = _allocate_campaign(
            root, controller, day=day, at_ns=bucket_ns, new_generation=False
        )
        manifest_sha = _sha(canonical(manifest).encode())
        plan = _bucket_plan(
            campaign_id=campaign_id,
            bucket=bucket,
            action="initial_bootstrap",
            requests=1,
            raw=1_000_000,
            stored=1_000_000,
            manifest_sha256=manifest_sha,
            predecessor_frontier_slot=controller.get("frontier_slot"),
            predecessor_frontier_receipt_ns=controller.get("frontier_receipt_ns"),
            predecessor_last_inventoried_interval=controller.get("last_inventoried_interval"),
            predecessor_gap_intervals=controller.get("gap_intervals", []),
            previous_bucket_ns=None,
        )
        plan["bootstrap_epoch_plan"] = _external_bootstrap_plan(
            campaign_id=campaign_id,
            bucket=bucket,
            seed=plan["seed"],
            service_manifest_sha256=manifest_sha,
        )
        plan["bootstrap_epoch_plan_sha256"] = _sha(canonical(plan["bootstrap_epoch_plan"]).encode())
        plan_sha256 = _sha(canonical(plan).encode())
        reservation_id = f"{campaign_id}:{bucket}:initial-bootstrap"
        _append(
            root,
            {
                "kind": "reservation",
                "reservation_id": reservation_id,
                "campaign_id": campaign_id,
                "bucket_ns": bucket_ns,
                "utc_date": day,
                "action": "initial_bootstrap",
                "plan": plan,
                "plan_sha256": plan_sha256,
                "requests": 1,
                "raw_bytes": 1_000_000,
                "stored_bytes": 1_000_000,
                "at_ns": at_ns,
            },
        )
        controller["bootstrap_reserved"] = True
        controller["last_bucket_ns"] = bucket_ns
        _write(root / "controller-v2.json", controller)
        _write(
            root / "writer-active-v2.json",
            {
                "reservation_id": reservation_id,
                "campaign_id": campaign_id,
                "bucket": bucket,
                "plan_sha256": plan_sha256,
                "status": "reserved_not_started",
            },
            exclusive=True,
        )
        return {
            "status": "reserved",
            "reservation_id": reservation_id,
            "campaign_id": campaign_id,
            "bucket_ns": bucket_ns,
            "action": "initial_bootstrap",
            "requests": INITIAL_BOOTSTRAP_REQUESTS,
            "plan": plan,
            "plan_sha256": plan_sha256,
        }
    finally:
        handle.close()


def reserve_bucket(
    root: Path,
    *,
    bucket_ns: int,
    free_disk_bytes: int | None = None,
    request_time_ns: int | None = None,
) -> dict[str, Any]:
    """Persist one frame or anchor-only epoch at a unique future hourly bucket."""
    handle = _lock(root)
    try:
        controller = _read(root / "controller-v2.json")
        manifest = _read(root / "service-manifest-v2.json")
        if (root / "STOP").exists():
            raise ValueError("service_stopped")
        if not controller["bootstrap_sealed"]:
            raise ValueError("bootstrap_must_be_reconciled_before_buckets")
        hour_ns = 3600 * 1_000_000_000
        tick = datetime.fromtimestamp(bucket_ns / 1e9, UTC)
        if bucket_ns % hour_ns:
            raise ValueError("bucket_must_be_utc_hour_boundary")
        now = time.time_ns() if request_time_ns is None else request_time_ns
        if now < bucket_ns or now >= bucket_ns + hour_ns:
            raise ValueError("tick_outside_hourly_bucket")
        if now >= controller["expires_at_ns"]:
            _stop(root, "service_lease_expired", at_ns=now)
            raise ValueError("service_lease_expired")
        if now < controller["authorized_at_ns"]:
            raise ValueError("service_not_yet_authorized")
        events = _verify_for_action(root, manifest, controller, at_ns=now)
        events = _recover_orphan_reservation(root, controller=controller, events=events, at_ns=now)
        if (root / "writer-active-v2.json").exists():
            raise ValueError("service_writer_pending_offline_reconciliation")
        previous = controller.get("last_bucket_ns")
        if previous is not None and bucket_ns <= previous:
            raise ValueError("bucket_identity_already_spent_or_nonmonotonic")
        bucket_ids = {
            event["bucket_ns"]
            for event in events
            if event.get("kind") == "reservation" and type(event.get("bucket_ns")) is int
        }
        if bucket_ns in bucket_ids:
            raise ValueError("bucket_identity_already_spent_or_reserved")
        day = tick.strftime("%Y%m%d")
        day_bucket_count = sum(
            1
            for value in bucket_ids
            if datetime.fromtimestamp(value / 1e9, UTC).strftime("%Y%m%d") == day
        )
        if day_bucket_count >= BUCKETS_PER_DAY:
            _stop(root, "daily_bucket_cap_reached", at_ns=now)
            raise ValueError("daily_bucket_cap_reached")
        missed = [] if previous is None else _missed(previous, bucket_ns)
        ambiguous_pending = _reset_pending(events)
        if ambiguous_pending and controller.get("ambiguous_recovery_used"):
            _stop(root, "second_ambiguous_transport_circuit_breaker", at_ns=now)
            raise ValueError("second_ambiguous_transport_circuit_breaker")
        needs_anchor = (
            bool(missed)
            or ambiguous_pending
            or controller.get("recovery_generation_pending", False)
            or controller.get("sleep_gap_pending", False)
            or controller.get("campaign_handoff_pending", False)
        )
        action = (
            "recovery_anchor"
            if ambiguous_pending
            else "sleep_gap_anchor"
            if needs_anchor
            else "frame"
        )
        previous_campaigns = list(controller.get("campaigns", []))
        adjacent_rollover = bool(
            action == "frame"
            and previous is not None
            and previous_campaigns
            and previous_campaigns[-1].get("utc_date") != day
            and bucket_ns == previous + 3600 * 1_000_000_000
        )
        if adjacent_rollover:
            campaign_id = _close_and_initialize_adjacent_rollover(
                root,
                controller=controller,
                events=events,
                day=day,
                bucket_ns=bucket_ns,
                prior_bucket_ns=previous,
            )
        day_campaigns = [c for c in controller.get("campaigns", []) if c["utc_date"] == day]
        new_generation = ambiguous_pending or (needs_anchor and bool(day_campaigns))
        if not adjacent_rollover:
            campaign_id = _allocate_campaign(
                root,
                controller,
                day=day,
                at_ns=bucket_ns,
                new_generation=new_generation,
            )
        request_cap = RESET_REQUESTS if needs_anchor else FRAME_REQUESTS
        raw_cap = 1_000_000 if needs_anchor else FRAME_RAW_BYTES
        stored_cap = 1_000_000 if needs_anchor else FRAME_STORED_BYTES
        usage = _usage(events)
        day_usage = _usage_for_day(events, day)
        if usage["outstanding"]:
            raise ValueError("prior_reservation_requires_offline_reconciliation")
        free_disk = shutil.disk_usage(root).free if free_disk_bytes is None else free_disk_bytes
        physical_bytes = _physical_bytes(root / "campaigns")
        if (
            free_disk < DISK_FLOOR_BYTES + DISK_PEAK_MARGIN_BYTES
            or physical_bytes + stored_cap > SERVICE_STORED_BYTES
        ):
            _stop(root, "disk_or_physical_budget_breach", at_ns=now)
            raise ValueError("disk_or_physical_budget_breach")
        if not _fits_budget(
            usage,
            requests=request_cap,
            raw_bytes=raw_cap,
            stored_bytes=stored_cap,
            request_cap=SERVICE_REQUESTS_MAX,
            raw_cap=SERVICE_RAW_BYTES,
            stored_cap=SERVICE_STORED_BYTES,
        ):
            _stop(root, "service_budget_exhausted", at_ns=now)
            raise ValueError("service_budget_exhausted")
        if not _fits_budget(
            day_usage,
            requests=request_cap,
            raw_bytes=raw_cap,
            stored_bytes=stored_cap,
            request_cap=DAILY_REQUESTS_MAX,
            raw_cap=DAILY_RAW_BYTES,
            stored_cap=DAILY_STORED_BYTES,
        ):
            _stop(root, "daily_budget_exhausted", at_ns=now)
            raise ValueError("daily_budget_exhausted")
        bucket = tick.strftime("%Y%m%dT%H0000Z")
        reservation_id = f"{campaign_id}:{bucket}:{action}"
        service_manifest_sha256 = _sha(canonical(manifest).encode())
        next_start = None if needs_anchor else controller["frontier_slot"] + 1
        plan = _bucket_plan(
            campaign_id=campaign_id,
            bucket=bucket,
            action=action,
            requests=request_cap,
            raw=raw_cap,
            stored=stored_cap,
            manifest_sha256=service_manifest_sha256,
            next_start=next_start,
            predecessor_frontier_slot=controller.get("frontier_slot"),
            predecessor_frontier_receipt_ns=controller.get("frontier_receipt_ns"),
            predecessor_last_inventoried_interval=controller.get("last_inventoried_interval"),
            predecessor_gap_intervals=controller.get("gap_intervals", []),
            previous_bucket_ns=previous,
            anchor_only_gap_reason=(
                "ambiguous_frame_uncovered_interval"
                if ambiguous_pending
                else "missed_schedule_no_backfill"
                if missed
                else "campaign_rollover_uncovered_interval"
                if controller.get("campaign_handoff_pending", False)
                else "sleep_gap_anchor_observation"
            ),
        )
        if action != "frame":
            plan["bootstrap_epoch_plan"] = _external_bootstrap_plan(
                campaign_id=campaign_id,
                bucket=bucket,
                seed=plan["seed"],
                service_manifest_sha256=service_manifest_sha256,
            )
            plan["bootstrap_epoch_plan_sha256"] = _sha(
                canonical(plan["bootstrap_epoch_plan"]).encode()
            )
        plan_sha256 = _sha(canonical(plan).encode())
        _append(
            root,
            {
                "kind": "reservation",
                "reservation_id": reservation_id,
                "campaign_id": campaign_id,
                "bucket_ns": bucket_ns,
                "utc_date": day,
                "action": action,
                "missed_buckets": missed,
                "preserve_frontier_slot": controller.get("frontier_slot"),
                "ambiguous_recovery": ambiguous_pending,
                "plan": plan,
                "plan_sha256": plan_sha256,
                "requests": request_cap,
                "raw_bytes": raw_cap,
                "stored_bytes": stored_cap,
                "at_ns": now,
            },
        )
        controller["last_bucket_ns"] = bucket_ns
        if needs_anchor and not ambiguous_pending:
            controller["recovery_generation_pending"] = False
        _write(root / "controller-v2.json", controller)
        _write(
            root / "writer-active-v2.json",
            {
                "reservation_id": reservation_id,
                "campaign_id": campaign_id,
                "bucket": bucket,
                "plan_sha256": plan_sha256,
                "status": "reserved_not_started",
            },
            exclusive=True,
        )
        return {
            "status": "reserved",
            "reservation_id": reservation_id,
            "campaign_id": campaign_id,
            "bucket_ns": bucket_ns,
            "action": action,
            "missed_buckets": missed,
            "requests": request_cap,
            "plan": plan,
            "plan_sha256": plan_sha256,
        }
    finally:
        handle.close()


def mark_epoch_started(root: Path, *, reservation_id: str, at_ns: int) -> dict[str, Any]:
    """Durably mark the handoff before invoking the v3 epoch scheduler."""
    handle = _lock(root)
    try:
        manifest = _read(root / "service-manifest-v2.json")
        controller = _read(root / "controller-v2.json")
        events = _verify_for_action(root, manifest, controller, at_ns=at_ns)
        writer_path = root / "writer-active-v2.json"
        writer = _read(writer_path)
        if writer.get("reservation_id") != reservation_id:
            raise ValueError("service_writer_reservation_mismatch")
        reservations = {e["reservation_id"]: e for e in events if e.get("kind") == "reservation"}
        reservation = reservations.get(reservation_id)
        if reservation is None or writer.get("status") != "reserved_not_started":
            raise ValueError("service_epoch_start_not_unique")
        writer.update({"status": "epoch_started", "started_ns": at_ns})
        _write(writer_path, writer)
        return writer
    finally:
        handle.close()


def _missed(previous_ns: int | None, current_ns: int) -> list[str]:
    if previous_ns is None:
        return []
    start = datetime.fromtimestamp((previous_ns + 3600 * 1_000_000_000) / 1e9, UTC)
    stop = datetime.fromtimestamp(current_ns / 1e9, UTC)
    return [
        (start + timedelta(hours=i)).strftime("%Y%m%dT%H0000Z")
        for i in range(int((stop - start).total_seconds() // 3600))
    ]


def _verified_epoch_evidence(
    root: Path, reservation: dict[str, Any], *, historical: bool = False
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Derive usage/outcome from the reserved v3 epoch tree and validate its full lineage."""
    if reservation.get("action") not in {
        "frame",
        "initial_bootstrap",
        "recovery_anchor",
        "sleep_gap_anchor",
    }:
        raise ValueError("epoch_adapter_reservation_action_invalid")
    campaign_root = root / "campaigns" / reservation["campaign_id"]
    campaign_manifest_path = campaign_root / "manifest-v2.json"
    schedule_manifest_path = campaign_root / "schedule-v3.json"
    campaign_manifest = _read(campaign_manifest_path)
    schedule_manifest = _read(schedule_manifest_path)
    if campaign_manifest.get("service_manifest_sha256") != _sha(
        (root / "service-manifest-v2.json").read_bytes()
    ):
        raise ValueError("epoch_adapter_service_manifest_binding_mismatch")
    if schedule_manifest.get("schedule_id") != reservation["campaign_id"]:
        raise ValueError("epoch_adapter_schedule_identity_mismatch")
    if schedule_manifest.get("code_sha256") != schedule_v3._bindings():
        raise ValueError("epoch_adapter_schedule_source_hash_mismatch")
    expected_schedule = {
        "provider": "helius-free",
        "interval_hours": 1,
        "frame_width_cap": FRAME_WIDTH_MAX,
        "selection_count": SELECTION_COUNT,
        "request_budget_per_epoch": FRAME_REQUESTS,
        "raw_byte_budget_per_epoch": FRAME_RAW_BYTES,
        "stored_byte_budget_per_epoch": FRAME_STORED_BYTES,
        "max_total_requests": DAILY_REQUESTS_MAX,
        "max_total_raw_bytes": DAILY_RAW_BYTES,
        "max_total_stored_bytes": DAILY_STORED_BYTES,
        "minimum_free_disk_bytes": DISK_FLOOR_BYTES,
        "capture_storage_codec": "zstd",
    }
    if any(schedule_manifest.get(key) != value for key, value in expected_schedule.items()):
        raise ValueError("epoch_adapter_schedule_policy_mismatch")
    bucket = reservation["plan"]["bucket"]
    epoch = campaign_root / "epochs" / bucket
    plan_path = epoch / "epoch-plan.json"
    reservation_path = epoch / "frame-reservation.json"
    summary_path = epoch / "epoch-summary.json"
    stop_path = epoch / "STOP"
    if not all(path.is_file() for path in (plan_path, reservation_path, summary_path, stop_path)):
        raise ValueError("epoch_adapter_sealed_tree_incomplete")
    plan = _read(plan_path)
    expected_plan = reservation["plan"]
    if (
        plan.get("schedule_id") != expected_plan["schedule_id"]
        or plan.get("bucket") != expected_plan["bucket"]
        or plan.get("seed") != expected_plan["seed"]
        or plan.get("selection_rule") != expected_plan["selection_rule"]
        or plan.get("frame_width_cap") != expected_plan["frame_width_cap"]
        or plan.get("selection_count") != SELECTION_COUNT
        or plan.get("next_start") != expected_plan.get("next_start")
        or plan.get("request_budget") != FRAME_REQUESTS
        or plan.get("raw_byte_budget") != FRAME_RAW_BYTES
        or plan.get("stored_byte_budget") != FRAME_STORED_BYTES
        or plan.get("minimum_free_disk_bytes") != expected_plan["minimum_free_disk_bytes"]
        or plan.get("capture_storage_codec") != expected_plan["capture_storage_codec"]
        or expected_plan.get("epoch_source_sha256") != schedule_v3._bindings()
        or schedule_manifest.get("code_sha256") != expected_plan.get("epoch_source_sha256")
        or plan.get("schedule_sha256") != _sha(canonical(schedule_manifest).encode())
        or plan.get("code_sha256")
        != schedule_v3._bindings()["tools/brain_senses_block_epoch_v2.py"]
        or plan.get("provider") != "helius-free"
        or plan.get("bootstrap") != (reservation["action"] != "frame")
    ):
        raise ValueError("epoch_adapter_plan_binding_mismatch")
    if plan.get("capture_storage_codec") != "zstd":
        raise ValueError("epoch_adapter_capture_codec_mismatch")
    frame_reservation = _read(reservation_path)
    if frame_reservation.get("plan_sha256") != _sha(plan_path.read_bytes()):
        raise ValueError("epoch_adapter_epoch_plan_hash_mismatch")
    if (
        frame_reservation.get("request_count") != FRAME_REQUESTS
        or frame_reservation.get("raw_bytes") != FRAME_RAW_BYTES
        or frame_reservation.get("stored_bytes") != FRAME_STORED_BYTES
    ):
        raise ValueError("epoch_adapter_epoch_reservation_mismatch")
    summary = schedule_v3._reconcile_epoch(epoch, plan, frame_reservation)
    stop = _read(stop_path)
    if summary.get("stop_sha256") != _sha(stop_path.read_bytes()) or stop.get(
        "status"
    ) != summary.get("status"):
        raise ValueError("epoch_adapter_stop_summary_mismatch")
    rows = epoch_v2._validate_journal(epoch, plan)
    active_path = campaign_root / "active-frame-reservation.json"
    release_path = epoch / "frame-reservation-release.json"
    frontier_path = campaign_root / "frontier-v3.json"
    if not all(path.is_file() for path in (active_path, release_path, frontier_path)):
        raise ValueError("epoch_adapter_scheduler_reconciliation_missing")
    active = _read(active_path)
    release = _read(release_path)
    frontier = _read(frontier_path)
    if (
        active.get("status") != "reconciled"
        or (active.get("bucket") != bucket and not historical)
        or (historical and active.get("bucket", "") < bucket)
        or (active.get("plan_sha256") != _sha(plan_path.read_bytes()) and not historical)
        or release.get("bucket") != bucket
        or release.get("summary_sha256") != _sha(summary_path.read_bytes())
        or (frontier.get("last_attempt_bucket") != bucket and not historical)
        or (historical and frontier.get("last_attempt_bucket", "") < bucket)
    ):
        raise ValueError("epoch_adapter_scheduler_reconciliation_mismatch")
    request_files = sorted((epoch / "requests").glob("*.json"))
    result_files = sorted((epoch / "request-results").glob("*.json"))
    if len(rows) != len(request_files) or len(rows) != len(result_files):
        raise ValueError("epoch_adapter_journal_accounting_mismatch")
    if not rows or rows[0].get("phase") != "anchor":
        raise ValueError("epoch_adapter_anchor_missing")
    anchor = rows[0]
    if plan.get("bootstrap"):
        if len(rows) != 1 or summary.get("status") not in {
            "bootstrap_complete",
            "anchor_failed",
            "anchor_ambiguous",
        }:
            raise ValueError("epoch_adapter_bootstrap_shape_mismatch")
        if anchor.get("status") == "captured" and anchor.get("slot_value") is not None:
            if summary.get("advance_to") != anchor.get("slot_value") + 1:
                raise ValueError("epoch_adapter_bootstrap_frontier_mismatch")
            if summary.get("status") == "bootstrap_complete" and (
                summary.get("bootstrap_slot") != anchor.get("slot_value")
                or summary.get("bootstrap_receipt_ns") != anchor.get("receipt_ns")
            ):
                raise ValueError("epoch_adapter_bootstrap_receipt_mismatch")
    elif (
        summary.get("status") == "anchor_regression"
        and anchor.get("status") == "captured"
        and anchor.get("result_status") == "parsed"
    ):
        if (
            summary.get("start") != plan.get("next_start")
            or summary.get("end") != anchor.get("slot_value") + 1
            or summary.get("end") >= summary.get("start")
            or summary.get("advance_to") is not None
            or len(rows) != 1
        ):
            raise ValueError("epoch_adapter_anchor_regression_mismatch")
    elif anchor.get("status") == "captured" and anchor.get("result_status") == "parsed":
        frame_path = epoch / "frame.json"
        if not frame_path.is_file():
            raise ValueError("epoch_adapter_frame_missing")
        frame = _read(frame_path)
        start, end = frame.get("start"), frame.get("end_exclusive")
        if (
            type(start) is not int
            or type(end) is not int
            or start != plan.get("next_start")
            or end != anchor.get("slot_value") + 1
            or frame.get("anchor_receipt_ns") != anchor.get("receipt_ns")
            or summary.get("start") != start
            or summary.get("end_exclusive") != end
            or summary.get("advance_to") != end
        ):
            raise ValueError("epoch_adapter_frame_frontier_mismatch")
        if 0 < end - start <= plan["frame_width_cap"]:
            inventory_rows = [row for row in rows if row.get("phase") == "inventory"]
            if end == start:
                if inventory_rows:
                    raise ValueError("epoch_adapter_empty_frame_has_inventory")
            elif inventory_rows and inventory_rows[0].get("status") == "captured":
                inventory = inventory_rows[0]
                members = epoch_v2._parse_inventory(inventory, start, end)
                if members is None:
                    raise ValueError("epoch_adapter_inventory_range_invalid")
                selection = _read(epoch / "selection-plan.json")
                expected_selection = epoch_v2._build_selection(
                    plan=plan,
                    inventory_slots=members,
                    inventory_sha256=inventory["response_sha256"],
                )
                if selection != expected_selection:
                    raise ValueError("epoch_adapter_selection_recomputation_mismatch")
                if summary.get("inventory_sha256") != inventory["response_sha256"]:
                    raise ValueError("epoch_adapter_inventory_hash_mismatch")
                if summary.get("inventory_denominator") != len(members):
                    raise ValueError("epoch_adapter_inventory_denominator_mismatch")
                if summary.get("selected_slots") != expected_selection["selected_slots"]:
                    raise ValueError("epoch_adapter_selected_members_mismatch")
        elif end - start > plan["frame_width_cap"]:
            if summary.get("status") != "oversized_frame" or len(rows) != 1:
                raise ValueError("epoch_adapter_oversized_frame_shape_mismatch")
    response_raw = response_stored = 0
    failed_rows: list[dict[str, Any]] = []
    captured_dirs: set[str] = set()
    for row in rows:
        if row.get("status") != "captured" or row.get("result_status") != "parsed":
            failed_rows.append(row)
            if row.get("status") == "received_persistence_failed":
                raise ValueError("epoch_adapter_received_payload_not_durable")
            continue
        capture_path = row.get("capture_path")
        if not isinstance(capture_path, str) or capture_path in captured_dirs:
            raise ValueError("epoch_adapter_capture_path_invalid")
        captured_dirs.add(capture_path)
        capture_root = epoch / capture_path
        capture = Capture.load(capture_root)
        if (
            digest(capture.response) != row.get("response_sha256")
            or len(capture.response) != row.get("response_bytes")
            or capture.method != row.get("method")
            or capture.source_alias != "helius-free"
        ):
            raise ValueError("epoch_adapter_capture_result_binding_mismatch")
        request = json.loads(capture.request_json)
        journal_reservation = _read(epoch / "requests" / f"{int(row['sequence']):012d}.json")
        if (
            request.get("method") != journal_reservation.get("method")
            or request.get("params") != journal_reservation.get("params")
            or request.get("id") != 1
        ):
            raise ValueError("epoch_adapter_capture_request_binding_mismatch")
        stored_path = capture_root / "response.json.zst"
        response_raw += len(capture.response)
        response_stored += stored_path.stat().st_size
        if response_stored > FRAME_STORED_BYTES or response_raw > FRAME_RAW_BYTES:
            raise ValueError("epoch_adapter_epoch_byte_cap_exceeded")
    actual_capture_dirs = {
        str(path.relative_to(epoch)) for path in (epoch / "captures").glob("*") if path.is_dir()
    }
    if actual_capture_dirs != captured_dirs:
        raise ValueError("epoch_adapter_unreferenced_or_missing_capture")
    if (
        summary.get("request_reservations") != len(rows)
        or summary.get("captured_response_bytes") != response_raw
        or summary.get("stored_response_bytes") != response_stored
    ):
        raise ValueError("epoch_adapter_summary_accounting_mismatch")
    physical = _physical_bytes(campaign_root)
    if physical > SERVICE_STORED_BYTES:
        raise ValueError("epoch_adapter_physical_campaign_cap_exceeded")
    campaign_usage = schedule_v3._usage_v3(campaign_root / "epochs")
    if (
        campaign_usage["requests"] > DAILY_REQUESTS_MAX
        or campaign_usage["raw_bytes"] + campaign_usage["reserved_raw_bytes"] > DAILY_RAW_BYTES
        or campaign_usage["stored_bytes"] + campaign_usage["reserved_stored_bytes"]
        > DAILY_STORED_BYTES
    ):
        raise ValueError("epoch_adapter_campaign_accounting_cap_exceeded")
    evidence: dict[str, Any] = {
        "bucket": reservation["plan"]["bucket"],
        "epoch_plan_sha256": _sha(plan_path.read_bytes()),
        "schedule_manifest_sha256": _sha(schedule_manifest_path.read_bytes()),
        "epoch_summary_sha256": _sha(summary_path.read_bytes()),
        "stop_sha256": _sha(stop_path.read_bytes()),
        "request_reservations": len(rows),
        "started_request_reservations": len(rows),
        "unstarted_request_capacity": max(0, reservation["requests"] - len(rows)),
        "raw_response_bytes": response_raw,
        "stored_response_bytes": response_stored,
        "physical_campaign_bytes": physical,
        "failed_request_sequences": [row.get("sequence") for row in failed_rows],
        "decision_time_ns": time.time_ns(),
    }
    campaign_stop_path = campaign_root / "STOP"
    if campaign_stop_path.is_file():
        evidence["campaign_stop_sha256"] = _sha(campaign_stop_path.read_bytes())
        evidence["campaign_stop_reason"] = _read(campaign_stop_path).get("reason")
    status = summary.get("status")
    if status in {"frame_complete", "bootstrap_complete"} and not failed_rows:
        outcome = (
            "complete"
            if status == "frame_complete"
            else "bootstrap_complete"
            if reservation["action"] == "initial_bootstrap"
            else "anchor_only"
        )
    elif any(row.get("status") == "request_failed" for row in failed_rows):
        if all(
            row.get("status") == "request_failed"
            and row.get("transport_category") in {"timeout", "dns", "tls", "connection", "other"}
            and row.get("http_status") is None
            for row in failed_rows
        ):
            outcome = "ambiguous"
        else:
            outcome = "halt_review"
    elif status == "body_budget_exhausted":
        outcome = "sampled_gap"
    else:
        outcome = "halt_review"
    if status in {"anchor_regression", "oversized_frame", "empty_frame"}:
        outcome = "halt_review" if status == "anchor_regression" else "sampled_gap"
    if outcome in {"complete", "sampled_gap"} and summary.get("inventory_sha256"):
        evidence["inventory_interval"] = {
            "start": summary.get("start"),
            "end_exclusive": summary.get("end_exclusive"),
            "inventory_sha256": summary.get("inventory_sha256"),
            "denominator": summary.get("inventory_denominator"),
        }
    evidence["epoch_status"] = status
    evidence["request_outcomes"] = [
        {
            "sequence": row.get("sequence"),
            "phase": row.get("phase"),
            "slot": row.get("slot"),
            "event_slot": row.get("event_slot"),
            "block_time_seconds": row.get("block_time_seconds"),
            "status": row.get("status"),
            "result_status": row.get("result_status"),
            "response_sha256": row.get("response_sha256"),
            "request_started_ns": row.get("request_started_ns"),
            "receipt_ns": row.get("receipt_ns"),
            "availability_ns": row.get("availability_ns"),
        }
        for row in rows
    ]
    evidence["anchor_receipt_ns"] = next(
        (row.get("receipt_ns") for row in rows if row.get("phase") == "anchor"), None
    )
    evidence["anchor_slot"] = next(
        (row.get("slot_value") for row in rows if row.get("phase") == "anchor"), None
    )
    evidence["frontier_advance_to"] = summary.get("advance_to")
    return outcome, evidence, summary


def _external_bootstrap_evidence(
    root: Path, reservation: dict[str, Any], controller: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Verify the service-owned v2 anchor epoch and derive immutable v3 lineage."""
    if reservation.get("plan", {}).get("epoch_source_sha256") != schedule_v3._bindings():
        raise ValueError("bootstrap_epoch_source_binding_mismatch")
    _verify_sources(_read(root / "service-manifest-v2.json"))
    campaign_root = root / "campaigns" / reservation["campaign_id"]
    bucket = reservation["plan"]["bucket"]
    epoch = campaign_root / "bootstrap-epochs" / bucket
    plan_path = epoch / "epoch-plan.json"
    request_paths = sorted((epoch / "requests").glob("*.json"))
    result_paths = sorted((epoch / "request-results").glob("*.json"))
    if not plan_path.is_file():
        if request_paths or result_paths:
            raise ValueError("bootstrap_epoch_without_pinned_plan")
        evidence = {
            "bucket": bucket,
            "started_request_reservations": 0,
            "unstarted_request_capacity": 1,
            "decision_time_ns": time.time_ns(),
            "proof": "bootstrap request journal absent before v2 network boundary",
        }
        return "unstarted", evidence, {}, None

    expected_plan = reservation["plan"].get("bootstrap_epoch_plan")
    expected_plan_hash = reservation["plan"].get("bootstrap_epoch_plan_sha256")
    if (
        not isinstance(expected_plan, dict)
        or _sha(canonical(expected_plan).encode()) != expected_plan_hash
        or _read(plan_path) != expected_plan
    ):
        raise ValueError("bootstrap_epoch_plan_binding_mismatch")
    if len(request_paths) > 1 or len(result_paths) > 1:
        raise ValueError("bootstrap_epoch_request_count_mismatch")
    if not request_paths and not result_paths:
        return (
            "unstarted",
            {
                "bucket": bucket,
                "epoch_plan_sha256": _sha(plan_path.read_bytes()),
                "started_request_reservations": 0,
                "unstarted_request_capacity": 1,
                "decision_time_ns": time.time_ns(),
                "proof": "v2 durable request journal absent before I/O boundary",
            },
            {},
            None,
        )
    stop_path = epoch / "STOP"
    summary_path = epoch / "epoch-summary.json"
    if not stop_path.is_file() or not summary_path.is_file():
        # A durable request with no result is an ambiguous charged attempt. v2's
        # journal validator seals this identity as ambiguous without sending it again.
        if len(request_paths) == 1 and not result_paths:
            try:
                epoch_v2._validate_journal(epoch, expected_plan)
            except epoch_v2._Terminal:
                pass
            if stop_path.is_file() and summary_path.is_file():
                return _external_bootstrap_evidence(root, reservation, controller)
        raise ValueError("bootstrap_epoch_not_terminal_or_reconcilable")

    if len(request_paths) == 1 and not result_paths:
        try:
            epoch_v2._validate_journal(epoch, expected_plan)
        except epoch_v2._Terminal:
            pass
        stop = _read(stop_path)
        summary = _read(summary_path)
        if (
            stop.get("status") != "anchor_ambiguous"
            or summary.get("stop_sha256") != _sha(stop_path.read_bytes())
            or summary.get("request_reservations") != 1
        ):
            raise ValueError("bootstrap_ambiguous_journal_reconciliation_mismatch")
        evidence = {
            "bucket": bucket,
            "epoch_plan_sha256": _sha(plan_path.read_bytes()),
            "request_sha256": _sha(request_paths[0].read_bytes()),
            "summary_sha256": _sha(summary_path.read_bytes()),
            "stop_sha256": _sha(stop_path.read_bytes()),
            "started_request_reservations": 1,
            "unstarted_request_capacity": 0,
            "request_reservations": 1,
            "raw_response_bytes": 0,
            "stored_response_bytes": 0,
            "decision_time_ns": time.time_ns(),
            "proof": (
                "durable request reservation without result; may be charged and never replayed"
            ),
        }
        return "ambiguous", evidence, summary, None

    request_count, raw_bytes, stored_bytes = epoch_v2._counts(epoch)
    if request_count != len(request_paths) or len(result_paths) != len(request_paths):
        raise ValueError("bootstrap_epoch_journal_count_mismatch")
    try:
        rows = epoch_v2._validate_journal(epoch, expected_plan)
    except epoch_v2._Terminal:
        rows = [json.loads(path.read_text()) for path in result_paths]
    if len(rows) != 1 or rows[0].get("phase") != "anchor":
        raise ValueError("bootstrap_epoch_anchor_identity_mismatch")
    row = rows[0]
    stop = _read(stop_path)
    summary = _read(summary_path)
    if (
        summary.get("stop_sha256") != _sha(stop_path.read_bytes())
        or stop.get("status") != summary.get("status")
        or summary.get("epoch_id") != bucket
        or summary.get("request_reservations") != request_count
        or summary.get("captured_response_bytes") != raw_bytes
        or summary.get("stored_response_bytes") != stored_bytes
    ):
        raise ValueError("bootstrap_epoch_stop_summary_or_accounting_mismatch")
    evidence: dict[str, Any] = {
        "bucket": bucket,
        "epoch_plan_sha256": _sha(plan_path.read_bytes()),
        "request_sha256": _sha(request_paths[0].read_bytes()) if request_paths else None,
        "result_sha256": _sha(result_paths[0].read_bytes()) if result_paths else None,
        "summary_sha256": _sha(summary_path.read_bytes()),
        "stop_sha256": _sha(stop_path.read_bytes()),
        "started_request_reservations": request_count,
        "unstarted_request_capacity": max(0, 1 - request_count),
        "raw_response_bytes": raw_bytes,
        "stored_response_bytes": stored_bytes,
        "request_reservations": request_count,
        "decision_time_ns": time.time_ns(),
    }
    if row.get("status") == "request_failed":
        if (
            row.get("transport_category") in {"timeout", "dns", "tls", "connection", "other"}
            and row.get("http_status") is None
        ):
            evidence["request_started_ns"] = row.get("request_started_ns")
            evidence["receipt_ns"] = row.get("receipt_ns")
            evidence["availability_ns"] = row.get("availability_ns")
            evidence["failed_request_sequence"] = row.get("sequence")
            return "ambiguous", evidence, summary, None
        return "halt_review", evidence, summary, None
    if row.get("status") != "captured" or row.get("result_status") != "parsed":
        return "halt_review", evidence, summary, None
    capture_root = epoch / str(row.get("capture_path"))
    expected_capture_rel = f"captures/{int(row.get('sequence', -1)):012d}"
    if row.get("capture_path") != expected_capture_rel:
        raise ValueError("bootstrap_epoch_capture_path_invalid")
    capture = Capture.load(capture_root)
    capture_slot = capture.result()
    stored_path = capture_root / "response.json.zst"
    request = json.loads(capture.request_json)
    if (
        digest(capture.response) != row.get("response_sha256")
        or len(capture.response) != row.get("response_bytes")
        or capture.method != "getSlot"
        or capture.source_alias != "helius-free"
        or capture.request_started_ns != row.get("request_started_ns")
        or capture.received_ns != row.get("receipt_ns")
        or type(row.get("availability_ns")) is not int
        or row.get("availability_ns") < row.get("receipt_ns")
        or request
        != json.loads(epoch_v2._request_body("getSlot", [{"commitment": "finalized"}]).decode())
        or stored_path.stat().st_size != row.get("stored_response_bytes")
        or capture_slot != row.get("slot_value")
    ):
        raise ValueError("bootstrap_epoch_capture_binding_mismatch")
    slot = row.get("slot_value")
    receipt_ns = row.get("receipt_ns")
    if (
        type(slot) is not int
        or slot < 1
        or type(receipt_ns) is not int
        or summary.get("status") != "bootstrap_complete"
        or summary.get("bootstrap_slot") != slot
        or summary.get("advance_to") != slot + 1
        or summary.get("bootstrap_receipt_ns") != receipt_ns
        or slot < reservation["plan"].get("predecessor_frontier_slot", 0)
        or raw_bytes > reservation["raw_bytes"]
        or stored_bytes > reservation["stored_bytes"]
    ):
        return "halt_review", evidence, summary, None
    evidence.update(
        {
            "anchor_slot": slot,
            "anchor_receipt_ns": receipt_ns,
            "request_started_ns": row.get("request_started_ns"),
            "availability_ns": row.get("availability_ns"),
            "event_time": None,
        }
    )
    capture_dirs = {
        str(path.relative_to(epoch)) for path in (epoch / "captures").glob("*") if path.is_dir()
    }
    if capture_dirs != {expected_capture_rel}:
        raise ValueError("bootstrap_epoch_capture_set_mismatch")
    previous_frontier = reservation["plan"]["predecessor_frontier_slot"] + 1
    gap_intervals = list(reservation["plan"].get("predecessor_gap_intervals", []))
    if slot + 1 > previous_frontier:
        reason = {
            "initial_bootstrap": "successor_bootstrap_uncovered_interval",
            "recovery_anchor": "ambiguous_epoch_uncovered_interval",
            "sleep_gap_anchor": "missed_schedule_uncovered_interval",
        }[reservation["action"]]
        gap_intervals.append(
            {"start": previous_frontier, "end_exclusive": slot + 1, "reason": reason}
        )
    lineage_evidence = {
        key: evidence.get(key)
        for key in (
            "epoch_plan_sha256",
            "request_sha256",
            "result_sha256",
            "summary_sha256",
            "stop_sha256",
            "anchor_slot",
            "anchor_receipt_ns",
            "request_started_ns",
            "availability_ns",
        )
    }
    lineage = {
        "schema_version": 1,
        "provider": "helius-free",
        "campaign_id": reservation["campaign_id"],
        "controller_lease_expires_ns": controller["expires_at_ns"],
        "last_observed_anchor_slot": slot,
        "last_observed_anchor_receipt_ns": receipt_ns,
        "last_attempt_bucket": bucket,
        "last_inventoried_interval": reservation["plan"].get(
            "predecessor_last_inventoried_interval"
        ),
        "gap_intervals": gap_intervals,
        "bootstrap_evidence_sha256": _sha(canonical(lineage_evidence).encode()),
        "service_manifest_sha256": _sha((root / "service-manifest-v2.json").read_bytes()),
        "epoch_plan_sha256": evidence["epoch_plan_sha256"],
        "schedule_source_sha256": schedule_v3._bindings(),
        "epoch_source_sha256": _sha(Path(epoch_v2.__file__).read_bytes()),
    }
    lineage_path = campaign_root / "predecessor-lineage-v1.json"
    if lineage_path.exists():
        if _read(lineage_path) != lineage:
            raise ValueError("service_predecessor_lineage_changed")
    else:
        _write(lineage_path, lineage, exclusive=True)
    schedule_manifest = schedule_v3.initialize(campaign_root, lineage_path)
    evidence["lineage_sha256"] = _sha(lineage_path.read_bytes())
    evidence["schedule_manifest_sha256"] = _sha(canonical(schedule_manifest).encode())
    outcome = (
        "bootstrap_complete" if reservation["action"] == "initial_bootstrap" else "anchor_only"
    )
    return outcome, evidence, summary, lineage


def _apply_seal_to_controller(
    controller: dict[str, Any], reservation: dict[str, Any], seal: dict[str, Any]
) -> None:
    outcome = seal["outcome"]
    evidence = seal.get("evidence", {})
    if outcome == "ambiguous" and reservation["action"] == "frame":
        controller["recovery_generation_pending"] = True
    elif outcome in {"bootstrap_complete", "anchor_only", "complete", "sampled_gap"}:
        summary = evidence.get("epoch_summary", {})
        advance_to = summary.get("advance_to")
        if type(advance_to) is int:
            controller["frontier_slot"] = advance_to - 1
        anchor_slot = evidence.get("anchor_slot")
        if type(anchor_slot) is int:
            controller["frontier_slot"] = anchor_slot
        receipt_ns = evidence.get("anchor_receipt_ns")
        if type(receipt_ns) is int:
            controller["frontier_receipt_ns"] = receipt_ns
        lineage = evidence.get("lineage")
        if isinstance(lineage, dict):
            controller["gap_intervals"] = list(lineage.get("gap_intervals", []))
            controller["last_inventoried_interval"] = lineage.get("last_inventoried_interval")
        if reservation["action"] == "initial_bootstrap":
            controller["bootstrap_sealed"] = outcome == "bootstrap_complete"
        if reservation["action"] in {"recovery_anchor", "sleep_gap_anchor"}:
            controller["recovery_generation_pending"] = False
            controller["sleep_gap_pending"] = False
            controller["campaign_handoff_pending"] = False
        if reservation["action"] == "recovery_anchor":
            controller["ambiguous_recovery_used"] = True
        if reservation["action"] == "frame" and evidence.get("campaign_stop_sha256"):
            controller["campaign_handoff_pending"] = True
    elif outcome == "unstarted":
        controller["sleep_gap_pending"] = True
        if reservation["action"] == "initial_bootstrap":
            controller["bootstrap_reserved"] = False
            controller["bootstrap_sealed"] = False
    controller["last_sealed_bucket_ns"] = reservation["bucket_ns"]


def _seal_from_epoch_locked(
    root: Path,
    *,
    reservation_id: str,
    at_ns: int,
    controller: dict[str, Any],
    manifest: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    reservations = {e["reservation_id"]: e for e in events if e.get("kind") == "reservation"}
    reservation = reservations.get(reservation_id)
    if reservation is None:
        raise ValueError("reservation_missing_or_already_sealed")
    existing_seal = next(
        (
            event
            for event in events
            if event.get("kind") == "seal" and event.get("reservation_id") == reservation_id
        ),
        None,
    )
    if existing_seal is not None:
        # Verify immutable evidence before recovering a crash after seal append.
        try:
            if reservation["action"] == "frame":
                outcome, evidence, summary = _verified_epoch_evidence(root, reservation)
                evidence["epoch_summary"] = summary
            else:
                outcome, evidence, summary, lineage = _external_bootstrap_evidence(
                    root, reservation, controller
                )
                if lineage is not None:
                    evidence["lineage"] = lineage
                    evidence["anchor_slot"] = lineage["last_observed_anchor_slot"]
                    evidence["anchor_receipt_ns"] = lineage["last_observed_anchor_receipt_ns"]
                    evidence["epoch_summary"] = summary
        except Exception as exc:
            _stop(root, "epoch_tree_reconciliation_failed_closed", at_ns=at_ns)
            raise ValueError("epoch_tree_reconciliation_failed_closed") from exc
        if outcome != existing_seal.get("outcome") or any(
            evidence.get(key) != existing_seal.get("evidence", {}).get(key)
            for key in (
                "epoch_plan_sha256",
                "schedule_manifest_sha256",
                "summary_sha256",
                "stop_sha256",
                "lineage_sha256",
                "campaign_stop_sha256",
            )
            if key in existing_seal.get("evidence", {})
        ):
            _stop(root, "sealed_epoch_evidence_changed", at_ns=at_ns)
            raise ValueError("sealed_epoch_evidence_changed")
        # Recover a crash after the append-only seal but before controller/marker updates.
        _apply_seal_to_controller(controller, reservation, existing_seal)
        _write(root / "controller-v2.json", controller)
        writer_path = root / "writer-active-v2.json"
        if writer_path.is_file():
            writer = _read(writer_path)
            if writer.get("reservation_id") != reservation_id or writer.get(
                "plan_sha256"
            ) != reservation.get("plan_sha256"):
                _stop(root, "service_writer_marker_uncertain", at_ns=at_ns)
                raise ValueError("service_writer_marker_uncertain")
        writer_path.unlink(missing_ok=True)
        return existing_seal
    writer_path = root / "writer-active-v2.json"
    writer = _read(writer_path) if writer_path.is_file() else None
    if (
        writer is None
        or writer.get("reservation_id") != reservation_id
        or writer.get("plan_sha256") != reservation.get("plan_sha256")
    ):
        _stop(root, "service_writer_marker_uncertain", at_ns=at_ns)
        raise ValueError("service_writer_marker_uncertain")

    try:
        if reservation["action"] == "frame":
            evidence_outcome, evidence, summary = _verified_epoch_evidence(root, reservation)
            evidence["epoch_summary"] = summary
            outcome = evidence_outcome
        else:
            outcome, evidence, summary, lineage = _external_bootstrap_evidence(
                root, reservation, controller
            )
            if lineage is not None:
                evidence["lineage"] = lineage
                evidence["anchor_slot"] = lineage["last_observed_anchor_slot"]
                evidence["anchor_receipt_ns"] = lineage["last_observed_anchor_receipt_ns"]
                evidence["epoch_summary"] = summary
    except Exception as exc:
        _stop(root, "epoch_tree_reconciliation_failed_closed", at_ns=at_ns)
        raise ValueError("epoch_tree_reconciliation_failed_closed") from exc

    if outcome == "halt_review":
        _stop(root, "provider_or_data_review_halt", at_ns=at_ns)
    if outcome == "ambiguous" and (
        reservation["action"] != "frame" or controller.get("ambiguous_recovery_used")
    ):
        _stop(root, "bootstrap_or_second_ambiguous_transport_circuit_breaker", at_ns=at_ns)
    if outcome == "unstarted":
        actual_requests = actual_raw = actual_stored = 0
    else:
        actual_requests = int(evidence.get("request_reservations", 0))
        actual_raw = int(evidence.get("raw_response_bytes", 0))
        actual_stored = int(evidence.get("stored_response_bytes", 0))

    seal = _append(
        root,
        {
            "kind": "seal",
            "reservation_id": reservation_id,
            "campaign_id": reservation["campaign_id"],
            "outcome": outcome,
            "actual_requests": actual_requests,
            "actual_raw_bytes": actual_raw,
            "actual_stored_bytes": actual_stored,
            "at_ns": at_ns,
            "evidence": evidence,
        },
    )
    _apply_seal_to_controller(controller, reservation, seal)
    _write(root / "controller-v2.json", controller)
    writer_path.unlink(missing_ok=True)
    dfd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return seal


def seal_from_epoch(root: Path, *, reservation_id: str, at_ns: int) -> dict[str, Any]:
    """Reconcile and seal a reserved v2 bootstrap or v3 frame without replay."""
    handle = _lock(root)
    try:
        controller = _read(root / "controller-v2.json")
        manifest = _read(root / "service-manifest-v2.json")
        events = _verify_for_action(root, manifest, controller, at_ns=at_ns)
        return _seal_from_epoch_locked(
            root,
            reservation_id=reservation_id,
            at_ns=at_ns,
            controller=controller,
            manifest=manifest,
            events=events,
        )
    finally:
        handle.close()


def run_reserved_bootstrap(
    root: Path,
    *,
    reservation_id: str,
    at_ns: int,
    clients: dict[str, Any],
) -> dict[str, Any]:
    """Run one injected-client bootstrap while holding service ownership through its seal."""
    handle = _lock(root)
    try:
        controller = _read(root / "controller-v2.json")
        manifest = _read(root / "service-manifest-v2.json")
        events = _verify_for_action(root, manifest, controller, at_ns=at_ns)
        if (root / "STOP").exists():
            raise ValueError("service_stopped")
        reservations = {e["reservation_id"]: e for e in events if e.get("kind") == "reservation"}
        reservation = reservations.get(reservation_id)
        if reservation is None or reservation.get("action") == "frame":
            raise ValueError("bootstrap_reservation_missing_or_invalid")
        if any(
            event.get("kind") == "seal" and event.get("reservation_id") == reservation_id
            for event in events
        ):
            return _seal_from_epoch_locked(
                root,
                reservation_id=reservation_id,
                at_ns=at_ns,
                controller=controller,
                manifest=manifest,
                events=events,
            )
        writer_path = root / "writer-active-v2.json"
        writer = _read(writer_path)
        if writer.get("reservation_id") != reservation_id or writer.get(
            "plan_sha256"
        ) != reservation.get("plan_sha256"):
            raise ValueError("service_writer_reservation_mismatch")
        hour_ns = 3600 * 1_000_000_000
        if at_ns < reservation["bucket_ns"]:
            raise ValueError("bootstrap_bucket_not_yet_open")
        if at_ns >= reservation["bucket_ns"] + hour_ns:
            return _seal_from_epoch_locked(
                root,
                reservation_id=reservation_id,
                at_ns=at_ns,
                controller=controller,
                manifest=manifest,
                events=events,
            )
        epoch = (
            root
            / "campaigns"
            / reservation["campaign_id"]
            / "bootstrap-epochs"
            / reservation["plan"]["bucket"]
        )
        epoch.mkdir(parents=True, exist_ok=True, mode=0o700)
        pinned = reservation["plan"]["bootstrap_epoch_plan"]
        plan_path = epoch / "epoch-plan.json"
        if plan_path.exists() and _read(plan_path) != pinned:
            _stop(root, "bootstrap_epoch_plan_changed", at_ns=at_ns)
            raise ValueError("bootstrap_epoch_plan_changed")
        if not plan_path.exists():
            _write(plan_path, pinned, exclusive=True)
        writer.update({"status": "epoch_started", "started_ns": at_ns})
        _write(writer_path, writer)
        epoch_v2.run_epoch(epoch, pinned, clients=clients)
        refreshed = _load_ledger(root)
        return _seal_from_epoch_locked(
            root,
            reservation_id=reservation_id,
            at_ns=at_ns,
            controller=_read(root / "controller-v2.json"),
            manifest=manifest,
            events=refreshed,
        )
    finally:
        handle.close()


def _sealed_timestamp_records(seals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project chain-event, request, receipt, availability, and decision times separately."""
    records: list[dict[str, Any]] = []
    for seal in seals:
        evidence = seal.get("evidence", {})
        request_outcomes = evidence.get("request_outcomes")
        has_request_rows = isinstance(request_outcomes, list) and bool(request_outcomes)
        if not has_request_rows:
            request_outcomes = [{}]
        for request in request_outcomes:
            request_started_ns = request.get("request_started_ns")
            receipt_ns = request.get("receipt_ns")
            availability_ns = request.get("availability_ns")
            if not has_request_rows:
                request_started_ns = evidence.get("request_started_ns")
                receipt_ns = evidence.get("anchor_receipt_ns")
                availability_ns = evidence.get("availability_ns")
            if seal.get("outcome") == "ambiguous" and request.get("status") == "request_failed":
                # Failed transport has no response receipt; some epoch adapters record
                # terminal bookkeeping timestamps under response-time field names.
                receipt_ns = None
                availability_ns = None
            event_slot = request.get("event_slot")
            if event_slot is None:
                event_slot = request.get("slot")
            if event_slot is None:
                event_slot = evidence.get("anchor_slot")
            block_time_seconds = request.get("block_time_seconds")
            records.append(
                {
                    "reservation_id": seal.get("reservation_id"),
                    "outcome": seal.get("outcome"),
                    "request_sequence": request.get("sequence"),
                    "request_phase": request.get("phase"),
                    "request_status": request.get("status"),
                    "native_slot": event_slot,
                    "block_time_seconds": block_time_seconds,
                    "request_started_ns": request_started_ns,
                    "request_start_time_state": "known"
                    if request_started_ns is not None
                    else "unknown",
                    "receipt_ns": receipt_ns,
                    "availability_ns": availability_ns,
                    "decision_time_ns": evidence.get("decision_time_ns", seal.get("at_ns")),
                    "event_time_state": "known" if block_time_seconds is not None else "unknown",
                    "receipt_time_state": "known" if receipt_ns is not None else "unknown",
                    "availability_time_state": (
                        "known" if availability_ns is not None else "unknown"
                    ),
                }
            )
    return records


def read_status(root: Path) -> dict[str, Any]:
    """Read-only state reconstruction; never creates files or queries a provider."""
    manifest = _read(root / "service-manifest-v2.json")
    controller = _read(root / "controller-v2.json")
    errors: list[str] = []
    try:
        _verify_campaigns(root, controller)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        events = _load_ledger(root)
        usage: dict[str, int] | None = _usage(events)
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc) or type(exc).__name__)
        events = []
        usage = None
    source_match = manifest.get("source_sha256") == _source_bindings()
    if not source_match:
        errors.append("service_source_hash_mismatch")
    stop = _read(root / "STOP") if (root / "STOP").exists() else None
    seals = [event for event in events if event["kind"] == "seal"]
    intervals = [
        event.get("evidence", {}).get("inventory_interval")
        for event in seals
        if event.get("evidence", {}).get("inventory_interval") is not None
    ]
    gaps = [
        {
            "campaign_id": event["campaign_id"],
            "reservation_id": event.get("reservation_id"),
            "reason": "missed_bucket_schedule",
            "buckets": event.get("missed_buckets", []),
        }
        for event in events
        if event["kind"] == "reservation" and event.get("missed_buckets")
    ]
    gaps.extend(
        {
            "campaign_id": event["campaign_id"],
            "reservation_id": event.get("reservation_id"),
            "reason": "ambiguous_transport_or_receipt",
            "epoch_status": event.get("evidence", {}).get("epoch_status"),
            "bucket": event.get("evidence", {}).get("bucket"),
        }
        for event in seals
        if event.get("outcome") == "ambiguous"
    )
    gaps.extend(
        {
            "campaign_id": event["campaign_id"],
            "reservation_id": event.get("reservation_id"),
            "reason": "scheduled_bucket_provably_unstarted",
            "bucket": event.get("evidence", {}).get("bucket"),
        }
        for event in seals
        if event.get("outcome") == "unstarted"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "service_id": controller["service_id"],
        "authorization_expires_ns": controller["expires_at_ns"],
        "campaign_ids": [item["campaign_id"] for item in controller.get("campaigns", [])],
        "ledger_events": len(events),
        "usage": usage,
        "reconciliation_errors": errors,
        "request_cap": SERVICE_REQUESTS_MAX,
        "raw_cap_provisional": SERVICE_RAW_BYTES,
        "stored_cap_provisional": SERVICE_STORED_BYTES,
        "physical_stored_bytes": _physical_bytes(root / "campaigns"),
        "free_disk_bytes": shutil.disk_usage(root).free,
        "disk_floor_bytes": DISK_FLOOR_BYTES,
        "ambiguous_recovery_used": controller.get("ambiguous_recovery_used", False),
        "campaign_generations": controller.get("campaigns", []),
        "frontier_slot": controller.get("frontier_slot"),
        "frontier_receipt_ns": controller.get("frontier_receipt_ns"),
        "recovery_generation_pending": controller.get("recovery_generation_pending", False),
        "campaign_handoff_pending": controller.get("campaign_handoff_pending", False),
        "writer_pending_reconciliation": (root / "writer-active-v2.json").is_file(),
        "last_bucket_ns": controller["last_bucket_ns"],
        "inventory_intervals": intervals,
        "missed_bucket_gaps": gaps,
        "sealed_evidence_timestamps": _sealed_timestamp_records(seals),
        "status": (
            "stopped"
            if stop
            else "review_required"
            if errors
            else "reconciliation_required"
            if usage is not None and usage["outstanding"]
            else "awaiting_bootstrap"
            if not controller["bootstrap_sealed"]
            else "policy_ready"
        ),
        "stop": stop,
        "credit_units": manifest["credit_units"],
        "source_bindings_match": manifest.get("source_sha256") == _source_bindings(),
    }


def _physical_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(p.stat().st_blocks * 512 for p in root.rglob("*") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="read local policy status without mutation or provider calls",
    )
    args = parser.parse_args(argv)
    if not args.status_only:
        parser.error("this offline milestone exposes read-only status only")
    print(canonical(read_status(args.root)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
