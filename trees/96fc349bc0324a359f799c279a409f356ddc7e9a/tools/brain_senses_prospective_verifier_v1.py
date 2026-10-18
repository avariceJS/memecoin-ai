#!/usr/bin/env python3
"""Read-only independent verifier for prospective-controller v1 lease evidence.

This parser intentionally does not import the controller implementation. It never
repairs a ledger, creates STOP, retries a request, or authorizes an adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

SCHEMA = "brain-senses-prospective-controller-v1"
ZERO_HASH = "0" * 64
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UTC_DAY_NS = 86_400_000_000_000  # Unix-epoch nanoseconds per UTC day.
MIN_DISK_FLOOR_BYTES = 50 * 1024**3


class VerificationError(ValueError):
    """Evidence is malformed, inconsistent, or outside the verified contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError("duplicate_json_key")
        result[key] = value
    return result


def _json(data: bytes, code: str) -> Any:
    try:
        return json.loads(data, object_pairs_hook=_unique_object)
    except VerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(code) from exc


def _integer(value: Any) -> bool:
    return type(value) is int


def _digest(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _read_regular(path: Path, *, owner: int | None = None, readonly: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise VerificationError("artifact_not_single_link_regular_file")
            if owner is not None and metadata.st_uid != owner:
                raise VerificationError("artifact_owner_invalid")
            if readonly and stat.S_IMODE(metadata.st_mode) & 0o222:
                raise VerificationError("artifact_not_read_only")
            data = stream.read()
            after = os.fstat(stream.fileno())
        current = path.lstat()
        if (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
            raise VerificationError("artifact_path_replaced_during_verification")
    except VerificationError:
        raise
    except OSError as exc:
        raise VerificationError("artifact_unavailable") from exc
    if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or len(data) != after.st_size:
        raise VerificationError("artifact_changed_during_verification")
    return data


def _validate_plan(plan: Any, *, expected_source_sha256: dict[str, str] | None) -> None:
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise VerificationError("plan_schema_invalid")
    if (
        not isinstance(plan.get("lease_id"), str)
        or not plan["lease_id"]
        or not _integer(plan.get("created_ns"))
        or not _integer(plan.get("expires_ns"))
        or plan["expires_ns"] <= plan["created_ns"]
    ):
        raise VerificationError("plan_identity_or_clock_invalid")
    caps = plan.get("caps")
    if not isinstance(caps, dict):
        raise VerificationError("plan_caps_invalid")
    for name in (
        "requests",
        "raw_bytes",
        "stored_bytes",
        "physical_bytes",
        "width",
        "daily_requests",
        "daily_raw_bytes",
        "daily_stored_bytes",
    ):
        if not _integer(caps.get(name)) or caps[name] <= 0:
            raise VerificationError("plan_cap_invalid")
    if caps["physical_bytes"] < MIN_DISK_FLOOR_BYTES:
        raise VerificationError("plan_disk_floor_below_minimum")
    sources = plan.get("source_sha256")
    if (
        not isinstance(sources, dict)
        or not sources
        or any(
            not isinstance(name, str) or not name or not _digest(value)
            for name, value in sources.items()
        )
    ):
        raise VerificationError("plan_source_bindings_invalid")
    if expected_source_sha256 is not None and sources != expected_source_sha256:
        raise VerificationError("expected_source_binding_mismatch")
    review = plan.get("review")
    if review is not None and (
        not isinstance(review, dict)
        or not isinstance(review.get("review_id"), str)
        or not review["review_id"]
        or not _digest(review.get("decision_sha256"))
    ):
        raise VerificationError("plan_review_binding_invalid")
    predecessor = plan.get("predecessor")
    if (
        not isinstance(predecessor, dict)
        or not _digest(predecessor.get("terminal_receipt_sha256"))
        or not _digest(predecessor.get("stop_sha256"))
        or not _integer(predecessor.get("frontier_slot"))
        or predecessor["frontier_slot"] < 0
        or not isinstance(predecessor.get("gaps"), list)
    ):
        raise VerificationError("plan_predecessor_binding_invalid")
    if predecessor.get("lease_id") == plan["lease_id"]:
        raise VerificationError("successor_lease_id_not_distinct")
    buckets = plan.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise VerificationError("plan_buckets_invalid")
    identifiers: set[str] = set()
    previous_start = plan["created_ns"]
    previous_end = plan["created_ns"]
    for bucket in buckets:
        if (
            not isinstance(bucket, dict)
            or not isinstance(bucket.get("bucket_id"), str)
            or not bucket["bucket_id"]
            or bucket["bucket_id"] in identifiers
            or bucket.get("kind") not in {"anchor", "frame"}
            or not _integer(bucket.get("start_ns"))
            or not _integer(bucket.get("end_ns"))
            or bucket["start_ns"] <= plan["created_ns"]
            or bucket["start_ns"] < previous_start
            or bucket["start_ns"] < previous_end
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > plan["expires_ns"]
        ):
            raise VerificationError("plan_bucket_invalid")
        identifiers.add(bucket["bucket_id"])
        previous_start = bucket["start_ns"]
        previous_end = bucket["end_ns"]
    if buckets[0]["kind"] != "anchor":
        raise VerificationError("plan_first_bucket_not_anchor")


def _read_events(path: Path) -> list[dict[str, Any]]:
    try:
        data = _read_regular(path, owner=os.getuid())
    except VerificationError as exc:
        if exc.args == ("artifact_unavailable",) and not path.exists():
            return []
        raise VerificationError("event_ledger_unavailable") from exc
    if not data:
        return []
    if not data.endswith(b"\n") or b"\r" in data:
        raise VerificationError("event_ledger_not_complete_jsonl")
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    for seq, line in enumerate(data[:-1].split(b"\n")):
        if not line:
            raise VerificationError("event_ledger_empty_line")
        event = _json(line, "event_json_invalid")
        if not isinstance(event, dict) or _canonical(event) + b"\n" != line + b"\n":
            raise VerificationError("event_line_not_canonical")
        body = dict(event)
        supplied = body.pop("event_sha256", None)
        if (
            not _integer(event.get("seq"))
            or event.get("seq") != seq
            or event.get("previous_sha256") != previous
            or not _digest(supplied)
            or supplied != _sha(_canonical(body))
        ):
            raise VerificationError("event_chain_sequence_or_hash_invalid")
        event["event_sha256"] = supplied
        previous = supplied
        events.append(event)
    return events


def _artifact_path(
    raw_path: Any, *, root: Path, allowed_roots: tuple[Path, ...], owner: int
) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise VerificationError("capture_path_invalid")
    path = Path(raw_path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("capture_artifact_unavailable") from exc
    candidates = (root, *allowed_roots)
    if not any(resolved == base or base in resolved.parents for base in candidates):
        raise VerificationError("capture_artifact_outside_allowlist")
    # Read the original entry too: resolving must not make a symlink acceptable.
    _read_regular(path, owner=owner, readonly=True)
    return path


def _usage(
    reservations: dict[str, dict[str, Any]], seals: dict[str, dict[str, Any]]
) -> dict[str, int]:
    totals = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    for request_id, reservation in reservations.items():
        seal = seals.get(request_id)
        if seal is None or seal.get("status") != "captured":
            fields = {
                "requests": "requests",
                "raw_bytes": "raw_bytes",
                "stored_bytes": "stored_bytes",
            }
            for metric, field in fields.items():
                totals[metric] += reservation[field]
        else:
            for metric in totals:
                totals[metric] += seal[f"actual_{metric}"]
    return totals


def _verify_predecessor(
    plan: dict[str, Any],
    previous_root: Path | None,
    *,
    allowed_artifact_roots: tuple[Path, ...],
) -> str:
    if previous_root is None:
        return "unverified"
    prior_root = previous_root.resolve(strict=True)
    terminal_path = prior_root / "TERMINAL.json"
    stop_path = prior_root / "STOP"
    terminal_bytes = _read_regular(terminal_path, owner=os.getuid())
    stop_bytes = _read_regular(stop_path, owner=os.getuid())
    pred = plan["predecessor"]
    if (
        _sha(terminal_bytes) != pred["terminal_receipt_sha256"]
        or _sha(stop_bytes) != pred["stop_sha256"]
    ):
        raise VerificationError("predecessor_hash_binding_mismatch")
    terminal = _json(terminal_bytes, "predecessor_terminal_invalid")
    stop = _json(stop_bytes, "predecessor_stop_invalid")
    if stop.get("reason") != "finite_lease_complete" or stop.get("status") != "stopped":
        return "abnormal_stop_rejected"
    body = dict(terminal)
    supplied = body.pop("terminal_sha256", None)
    if (
        terminal.get("status") != "lease_closed"
        or terminal.get("schema") != f"{SCHEMA}-terminal-v1"
        or supplied != _sha(_canonical(body))
        or terminal.get("stop_sha256") != _sha(stop_bytes)
        or terminal.get("closed_ns") != stop.get("stopped_ns")
        or terminal.get("frontier_slot") != pred["frontier_slot"]
        or terminal.get("gaps") != pred["gaps"]
    ):
        raise VerificationError("predecessor_terminal_semantics_invalid")
    if pred.get("lease_id") is not None and terminal.get("lease_id") != pred["lease_id"]:
        raise VerificationError("predecessor_lease_id_mismatch")
    if (
        plan["created_ns"] < terminal["closed_ns"]
        or plan["buckets"][0]["start_ns"] < terminal["closed_ns"]
    ):
        raise VerificationError("successor_created_before_predecessor_terminal")
    prior_verification = verify_lease(
        prior_root,
        now_ns=terminal["closed_ns"],
        allowed_artifact_roots=allowed_artifact_roots,
    )
    if (
        not prior_verification.get("valid")
        or prior_verification.get("state") != "closed"
        or not prior_verification.get("normal_terminal_verified")
    ):
        raise VerificationError("predecessor_lease_not_independently_verified")
    return "normal_terminal_verified"


def verify_lease(
    root: Path,
    *,
    now_ns: int,
    expected_source_sha256: dict[str, str] | None = None,
    expected_review_sha256: str | None = None,
    previous_root: Path | None = None,
    allowed_artifact_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Verify a lease without mutation; report only a recovery recommendation.

    Source hashes in the plan are shape-checked and can be compared with an
    independently supplied digest map. This verifier does not locate source files
    or authenticate reviewer identity. Capture paths must resolve under the lease
    root or an explicitly supplied artifact allowlist.
    """
    requested_root = Path(root)
    try:
        if not _integer(now_ns):
            raise VerificationError("verification_clock_invalid")
        root = requested_root.resolve(strict=True)
        metadata = requested_root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise VerificationError("lease_root_owner_mode_or_path_invalid")
        allowed = tuple(Path(item).resolve(strict=True) for item in allowed_artifact_roots)
        plan_bytes = _read_regular(root / "plan-v1.json", owner=os.getuid())
        plan = _json(plan_bytes, "plan_json_invalid")
        if not isinstance(plan, dict) or _canonical(plan) + b"\n" != plan_bytes:
            raise VerificationError("plan_not_canonical")
        _validate_plan(plan, expected_source_sha256=expected_source_sha256)
        if expected_review_sha256 is not None:
            review = plan.get("review")
            if (
                not _digest(expected_review_sha256)
                or not isinstance(review, dict)
                or review.get("decision_sha256") != expected_review_sha256
            ):
                raise VerificationError("expected_review_binding_mismatch")
        plan_sha = _sha(plan_bytes)
        events = _read_events(root / "events-v1.jsonl")
        if (
            not events
            or events[0].get("kind") != "initialized"
            or not _integer(events[0].get("seq"))
            or events[0].get("seq") != 0
        ):
            raise VerificationError("ledger_initialization_missing")
        initialized_ns = events[0].get("at_ns")
        if (
            events[0].get("plan_sha256") != plan_sha
            or not _integer(initialized_ns)
            or initialized_ns < plan["created_ns"]
            or initialized_ns >= plan["buckets"][0]["start_ns"]
        ):
            raise VerificationError("ledger_plan_binding_mismatch")

        buckets = {item["bucket_id"]: item for item in plan["buckets"]}
        reservations: dict[str, dict[str, Any]] = {}
        starts: dict[str, dict[str, Any]] = {}
        captures: dict[str, dict[str, Any]] = {}
        results: dict[str, dict[str, Any]] = {}
        seals: dict[str, dict[str, Any]] = {}
        gaps: dict[str, dict[str, Any]] = {}
        stop_event: dict[str, Any] | None = None
        latest_gap_seq = -1
        latest_anchor_seq = -1
        root_owner = os.getuid()

        for event in events:
            kind = event.get("kind")
            seq = event["seq"]
            if kind not in {
                "initialized",
                "reservation",
                "started",
                "capture",
                "result",
                "seal",
                "gap",
                "stop",
            }:
                raise VerificationError("event_kind_unsupported")
            at = event.get("at_ns")
            if kind in {
                "initialized",
                "reservation",
                "started",
                "seal",
                "gap",
                "stop",
            } and not _integer(at):
                raise VerificationError("event_time_missing")
            if stop_event is not None:
                raise VerificationError("event_after_stop")
            failed_request_ids = {
                request_id
                for request_id, result in results.items()
                if result.get("status") in {"failed", "unknown"}
            }
            if failed_request_ids and not (
                kind == "stop" or (kind == "seal" and event.get("request_id") in failed_request_ids)
            ):
                raise VerificationError("event_after_failed_or_unknown_result")

            if kind == "initialized":
                if seq != 0 or len(events) < 1:
                    raise VerificationError("duplicate_initialization")
                continue
            if seq == 0:
                raise VerificationError("ledger_missing_initialization")

            if kind == "reservation":
                request_id = event.get("request_id")
                bucket_id = event.get("bucket_id")
                bucket = buckets.get(bucket_id)
                if not isinstance(request_id, str) or not request_id or request_id in reservations:
                    raise VerificationError("reservation_identity_invalid")
                if bucket is None or event.get("bucket_kind") != bucket["kind"]:
                    raise VerificationError("reservation_bucket_binding_invalid")
                if not bucket["start_ns"] <= at < bucket["end_ns"] or at >= plan["expires_ns"]:
                    raise VerificationError("reservation_outside_schedule")
                if (
                    any(old["bucket_id"] == bucket_id for old in reservations.values())
                    or bucket_id in gaps
                ):
                    raise VerificationError("bucket_reserved_or_gapped_more_than_once")
                if len(reservations) != len(seals):
                    raise VerificationError("reservation_while_prior_request_unsealed")
                if any(
                    result.get("status") in {"failed", "unknown"} for result in results.values()
                ):
                    raise VerificationError("event_after_failed_or_unknown_result")
                for missed in plan["buckets"]:
                    if missed["end_ns"] <= at and missed["bucket_id"] != bucket_id:
                        if missed["bucket_id"] not in gaps and not any(
                            item["bucket_id"] == missed["bucket_id"]
                            for item in reservations.values()
                        ):
                            raise VerificationError("missed_bucket_not_recorded_as_gap")
                if latest_gap_seq > latest_anchor_seq and bucket["kind"] != "anchor":
                    raise VerificationError("gap_requires_anchor_reset")
                if bucket["kind"] == "frame" and latest_anchor_seq < 0:
                    raise VerificationError("frame_before_sealed_anchor")
                for field, _cap in (
                    ("requests", "requests"),
                    ("raw_bytes", "raw_bytes"),
                    ("stored_bytes", "stored_bytes"),
                    ("width", "width"),
                    ("physical_free_bytes", "physical_bytes"),
                    ("utc_day_index", "daily_requests"),
                ):
                    if not _integer(event.get(field)):
                        raise VerificationError("reservation_quantity_invalid")
                if not 1 <= event["requests"] <= plan["caps"]["requests"]:
                    raise VerificationError("reservation_request_count_invalid")
                if any(
                    event[key] < 0 or event[key] > plan["caps"][key]
                    for key in ("raw_bytes", "stored_bytes", "width")
                ):
                    raise VerificationError("reservation_exceeds_cap")
                if event["physical_free_bytes"] < plan["caps"]["physical_bytes"]:
                    raise VerificationError("reservation_disk_floor_failed")
                if event["utc_day_index"] != at // UTC_DAY_NS:
                    raise VerificationError("reservation_utc_day_mismatch")
                if (
                    not isinstance(event.get("method"), str)
                    or not event["method"]
                    or not _digest(event.get("request_sha256"))
                ):
                    raise VerificationError("reservation_request_binding_invalid")
                reservations[request_id] = event
                totals = _usage(reservations, seals)
                if any(totals[name] > plan["caps"][name] for name in totals):
                    raise VerificationError("lease_budget_exceeded")
                day_events = {
                    rid: row
                    for rid, row in reservations.items()
                    if row["utc_day_index"] == event["utc_day_index"]
                }
                day_usage = _usage(
                    day_events, {rid: row for rid, row in seals.items() if rid in day_events}
                )
                for metric, cap_name in (
                    ("requests", "daily_requests"),
                    ("raw_bytes", "daily_raw_bytes"),
                    ("stored_bytes", "daily_stored_bytes"),
                ):
                    if day_usage[metric] > plan["caps"][cap_name]:
                        raise VerificationError("utc_day_budget_exceeded")

            elif kind == "started":
                rid = event.get("request_id")
                reservation = reservations.get(rid)
                bucket = buckets.get(reservation["bucket_id"]) if reservation else None
                if (
                    reservation is None
                    or rid in starts
                    or at < reservation["at_ns"]
                    or at >= min(bucket["end_ns"], plan["expires_ns"])
                    or event.get("method") != reservation["method"]
                    or event.get("request_sha256") != reservation["request_sha256"]
                    or not _integer(event.get("physical_free_bytes"))
                    or event["physical_free_bytes"] < plan["caps"]["physical_bytes"]
                ):
                    raise VerificationError("started_event_invalid")
                starts[rid] = event

            elif kind == "capture":
                rid = event.get("request_id")
                reservation = reservations.get(rid)
                started = starts.get(rid)
                if reservation is None or started is None or rid in captures:
                    raise VerificationError("capture_without_unique_started_request")
                receipt = event.get("receipt_ns")
                availability = event.get("availability_ns")
                raw_bytes = event.get("raw_bytes")
                if (
                    not _integer(receipt)
                    or not _integer(availability)
                    or availability < receipt
                    or receipt < started["at_ns"]
                    or not _integer(raw_bytes)
                    or raw_bytes < 0
                    or raw_bytes > reservation["raw_bytes"]
                    or event.get("decision_ns") is not None
                    or not isinstance(event.get("event_time"), dict)
                    or not _digest(event.get("artifact_sha256"))
                ):
                    raise VerificationError("capture_time_or_accounting_invalid")
                path = _artifact_path(
                    event.get("artifact_path"), root=root, allowed_roots=allowed, owner=root_owner
                )
                artifact = _read_regular(path, owner=root_owner, readonly=True)
                if len(artifact) != raw_bytes or _sha(artifact) != event["artifact_sha256"]:
                    raise VerificationError("capture_artifact_hash_or_length_mismatch")
                captures[rid] = event

            elif kind == "result":
                rid = event.get("request_id")
                reservation = reservations.get(rid)
                started = starts.get(rid)
                if reservation is None or started is None or rid in results:
                    raise VerificationError("result_without_unique_reservation")
                actuals = ("actual_requests", "actual_raw_bytes", "actual_stored_bytes")
                if any(
                    not _integer(event.get(field))
                    or event[field] < 0
                    or event[field] > reservation[reserve]
                    for field, reserve in zip(
                        actuals, ("requests", "raw_bytes", "stored_bytes"), strict=True
                    )
                ):
                    raise VerificationError("result_accounting_invalid")
                status = event.get("status")
                if status not in {"captured", "failed", "unknown"}:
                    raise VerificationError("result_status_invalid")
                if status == "captured":
                    capture = captures.get(rid)
                    if (
                        capture is None
                        or started is None
                        or event["actual_requests"] < 1
                        or event["actual_raw_bytes"] != capture["raw_bytes"]
                        or event.get("capture_sha256") != capture["artifact_sha256"]
                        or event.get("receipt_ns") != capture["receipt_ns"]
                        or event.get("availability_ns") != capture["availability_ns"]
                        or event["receipt_ns"] < started["at_ns"]
                    ):
                        raise VerificationError("captured_result_binding_invalid")
                else:
                    if rid in captures:
                        raise VerificationError("noncaptured_result_has_capture")
                    if event.get("availability_ns") is not None and (
                        not _integer(event["availability_ns"])
                        or event["availability_ns"] < event.get("receipt_ns", 0)
                    ):
                        raise VerificationError("failed_result_time_invalid")
                if not _integer(event.get("receipt_ns")) or event["receipt_ns"] < started["at_ns"]:
                    raise VerificationError("result_receipt_time_invalid")
                results[rid] = event

            elif kind == "seal":
                rid = event.get("request_id")
                reservation = reservations.get(rid)
                result = results.get(rid)
                if reservation is None or result is None or rid in seals:
                    raise VerificationError("seal_without_unique_result")
                if (
                    event.get("bucket_id") != reservation["bucket_id"]
                    or event.get("bucket_kind") != reservation["bucket_kind"]
                    or event.get("status") != result["status"]
                    or any(
                        event.get(f"actual_{metric}") != result.get(f"actual_{metric}")
                        for metric in ("requests", "raw_bytes", "stored_bytes")
                    )
                    or at < result["receipt_ns"]
                    or (result["status"] == "captured" and at < captures[rid]["availability_ns"])
                ):
                    raise VerificationError("seal_result_binding_invalid")
                seals[rid] = event
                if event["status"] == "captured" and event["bucket_kind"] == "anchor":
                    slot = captures[rid].get("event_time", {}).get("slot")
                    if not _integer(slot) or slot < 0:
                        raise VerificationError("captured_anchor_slot_invalid")
                    latest_anchor_seq = seq

            elif kind == "gap":
                bucket_id = event.get("bucket_id")
                bucket = buckets.get(bucket_id)
                if (
                    bucket is None
                    or bucket_id in gaps
                    or any(r["bucket_id"] == bucket_id for r in reservations.values())
                    or len(reservations) != len(seals)
                    or at < bucket["end_ns"]
                    or not isinstance(event.get("reason"), str)
                    or not event["reason"]
                ):
                    raise VerificationError("gap_event_invalid")
                gaps[bucket_id] = event
                latest_gap_seq = seq

            elif kind == "stop":
                if not isinstance(event.get("reason"), str) or not event["reason"]:
                    raise VerificationError("stop_reason_invalid")
                stop_event = event

        latest_durable_time = max(
            (
                value
                for event in events
                for field in ("at_ns", "receipt_ns", "availability_ns")
                if field != "at_ns" or event["kind"] != "stop"
                if _integer(value := event.get(field))
            ),
            default=plan["created_ns"],
        )
        usage = _usage(reservations, seals)
        stop_path = root / "STOP"
        terminal_path = root / "TERMINAL.json"
        stop_marker = None
        if stop_path.exists():
            stop_bytes = _read_regular(stop_path, owner=root_owner)
            stop_marker = _json(stop_bytes, "stop_marker_invalid")
            if (
                not isinstance(stop_marker, dict)
                or stop_marker.get("schema") != SCHEMA
                or stop_marker.get("status") != "stopped"
                or not isinstance(stop_marker.get("reason"), str)
                or not stop_marker["reason"]
                or not _integer(stop_marker.get("stopped_ns"))
            ):
                raise VerificationError("stop_marker_shape_invalid")
            if stop_marker["stopped_ns"] < latest_durable_time:
                raise VerificationError("stop_time_precedes_durable_event")
        if stop_event is not None and stop_event["at_ns"] < latest_durable_time:
            raise VerificationError("stop_time_precedes_durable_event")
        if (
            stop_event
            and stop_marker
            and (
                stop_event.get("reason") != stop_marker.get("reason")
                or stop_event.get("at_ns") != stop_marker.get("stopped_ns")
            )
        ):
            raise VerificationError("stop_marker_ledger_mismatch")

        terminal = None
        if terminal_path.exists():
            terminal_bytes = _read_regular(terminal_path, owner=root_owner)
            terminal = _json(terminal_bytes, "terminal_receipt_invalid")
            body = dict(terminal) if isinstance(terminal, dict) else {}
            supplied = body.pop("terminal_sha256", None)
            prior_frontier = plan["predecessor"]["frontier_slot"]
            anchor_slots = [
                captures[rid]["event_time"]["slot"]
                for rid, seal in seals.items()
                if seal["status"] == "captured" and seal["bucket_kind"] == "anchor"
            ]
            expected_frontier = max(
                prior_frontier, max(anchor_slots, default=prior_frontier - 1) + 1
            )
            expected_gaps = [
                *plan["predecessor"]["gaps"],
                *[
                    {"bucket_id": row["bucket_id"], "at_ns": row["at_ns"], "reason": row["reason"]}
                    for row in events
                    if row["kind"] == "gap"
                ],
            ]
            if (
                not isinstance(terminal, dict)
                or terminal.get("schema") != f"{SCHEMA}-terminal-v1"
                or terminal.get("status") != "lease_closed"
                or supplied != _sha(_canonical(body))
                or terminal.get("lease_id") != plan["lease_id"]
                or terminal.get("plan_sha256") != plan_sha
                or terminal.get("ledger_head_sha256") != events[-1]["event_sha256"]
                or not stop_marker
                or terminal.get("stop_sha256") != _sha(stop_bytes)
                or stop_marker.get("reason") != "finite_lease_complete"
                or terminal.get("closed_ns") != stop_marker.get("stopped_ns")
                or not stop_event
                or stop_event.get("reason") != "finite_lease_complete"
                or terminal.get("frontier_slot") != expected_frontier
                or terminal.get("gaps") != expected_gaps
                or terminal.get("usage") != usage
                or len(reservations) != len(seals)
                or {
                    seal["bucket_id"] for seal in seals.values() if seal.get("status") == "captured"
                }
                | set(gaps)
                != set(buckets)
                or terminal.get("closed_ns", -1) < latest_durable_time
            ):
                raise VerificationError("terminal_receipt_or_frontier_invalid")

        predecessor_state = _verify_predecessor(plan, previous_root, allowed_artifact_roots=allowed)
        outstanding = set(reservations) - set(seals)
        started_unresolved = (set(starts) | {rid for rid, capture in captures.items()}) - set(
            results
        )
        if terminal is not None:
            recommendation = "closed_requires_new_reviewed_successor"
            state = "closed"
        elif stop_marker and not stop_event:
            recommendation = "reconcile_stop_ledger_only"
            state = "stop_marker_without_event"
        elif stop_event and not stop_marker:
            recommendation = "restore_stop_marker_only"
            state = "stop_event_without_marker"
        elif stop_marker or stop_event:
            recommendation = "stopped_no_successor"
            state = "stopped"
        elif started_unresolved:
            recommendation = "stop_ambiguous_started_request_no_replay"
            state = "started_ambiguous"
        elif outstanding:
            recommendation = "stop_outstanding_reservation_no_replay"
            state = "reservation_outstanding"
        elif any(result["status"] in {"failed", "unknown"} for result in results.values()):
            recommendation = "stop_failed_or_unknown_no_replay"
            state = "failed_or_unknown"
        elif _integer(now_ns) and now_ns >= plan["expires_ns"]:
            recommendation = "stop_expired_lease_no_renewal"
            state = "expired"
        else:
            recommendation = "ledger_consistent_offline_only"
            state = "open_consistent"

        cancelled_unstarted = []
        if (
            stop_marker
            and stop_marker.get("reason") == "owner_cancelled_before_schedule_exhaustion"
        ):
            observed_bucket_ids = {event["bucket_id"] for event in reservations.values()} | set(
                gaps
            )
            cancelled_unstarted = [
                bucket_id for bucket_id in buckets if bucket_id not in observed_bucket_ids
            ]

        return {
            "schema": f"{SCHEMA}-verifier-v1",
            "valid": True,
            "lease_id": plan["lease_id"],
            "plan_sha256": plan_sha,
            "event_count": len(events),
            "ledger_head_sha256": events[-1]["event_sha256"],
            "usage": usage,
            "reservation_count": len(reservations),
            "sealed_count": len(seals),
            "gap_count": len(gaps),
            "cancelled_unstarted": cancelled_unstarted,
            "state": state,
            "recovery_recommendation": recommendation,
            "predecessor_state": predecessor_state,
            "source_hashes_shape_checked": True,
            "source_hashes_matched_external_expectation": expected_source_sha256 is not None,
            "review_hash_matched_external_expectation": expected_review_sha256 is not None,
            "normal_terminal_verified": terminal is not None,
            "activation_authorized": False,
        }
    except VerificationError as exc:
        return {
            "schema": f"{SCHEMA}-verifier-v1",
            "valid": False,
            "state": "evidence_uncertain",
            "error": str(exc),
            "recovery_recommendation": "stop_and_never_replay_ambiguous_request",
            "normal_terminal_verified": False,
            "activation_authorized": False,
        }
    except (OSError, KeyError, TypeError, ValueError, IndexError) as exc:
        return {
            "schema": f"{SCHEMA}-verifier-v1",
            "valid": False,
            "state": "evidence_uncertain",
            "error": f"unhandled_evidence_shape:{type(exc).__name__}",
            "recovery_recommendation": "stop_and_never_replay_ambiguous_request",
            "normal_terminal_verified": False,
            "activation_authorized": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="one lease root created by controller v1")
    parser.add_argument("--now-ns", type=int, default=time.time_ns())
    parser.add_argument("--previous-root", type=Path)
    parser.add_argument("--allow-artifact-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--source-bindings-json",
        type=Path,
        help="JSON object of independently computed source name to SHA-256 bindings",
    )
    parser.add_argument("--review-sha256", help="independently supplied review decision digest")
    args = parser.parse_args(argv)
    expected_sources = None
    if args.source_bindings_json is not None:
        try:
            expected_sources = _json(
                args.source_bindings_json.read_bytes(), "expected_source_bindings_invalid"
            )
            if not isinstance(expected_sources, dict):
                raise VerificationError("expected_source_bindings_not_object")
        except (OSError, VerificationError) as exc:
            print(
                json.dumps(
                    {
                        "schema": f"{SCHEMA}-verifier-v1",
                        "valid": False,
                        "state": "evidence_uncertain",
                        "error": str(exc),
                        "recovery_recommendation": "stop_and_never_replay_ambiguous_request",
                        "normal_terminal_verified": False,
                        "activation_authorized": False,
                    },
                    sort_keys=True,
                )
            )
            return 2
    result = verify_lease(
        args.root,
        now_ns=args.now_ns,
        expected_source_sha256=expected_sources,
        expected_review_sha256=args.review_sha256,
        previous_root=args.previous_root,
        allowed_artifact_roots=tuple(args.allow_artifact_root),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
