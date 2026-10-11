"""Compile a nonauthorizing external Senses v2 successor candidate from a sealed v1 lease.

This module only reads held evidence and returns an in-memory object. It does not
write a plan, load credentials, contact a provider, or authorize activation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from tools import brain_senses_external_orchestrator_v1 as engine
from tools import brain_senses_external_schema_v1 as schema
from tools import brain_senses_external_verify_v1 as verifier


class SuccessorRefused(ValueError):
    """The supplied v1 evidence or proposed successor is not admissible."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_MINUTE_NS = 60_000_000_000
_NS = 1_000_000_000
_SERVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def _fail(reason: str) -> None:
    raise SuccessorRefused(reason)


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _bucket(start_ns: int) -> str:
    try:
        return datetime.fromtimestamp(start_ns // _NS, UTC).strftime("%Y%m%dT%H%M00Z")
    except (OverflowError, OSError, ValueError) as exc:
        raise SuccessorRefused("schedule_time_invalid") from exc


def _snapshot_before_verify(reader: Any) -> tuple[set[str], dict[str, bytes], dict[str, Any]]:
    files = set(reader.list_files())
    try:
        plan_bytes = reader.read_bytes(schema.PLAN_FILE)
        plan = verifier.decode(plan_bytes)
        paths = {schema.PLAN_FILE, schema.EVENTS_FILE}
        buckets = [plan["schedule"][phase]["bucket"] for phase in ("bootstrap", "frame")]
        for bucket in buckets:
            base = f"buckets/{bucket}"
            paths.update({base + "/bundle.json", base + "/seal.json"})
            for path in files:
                if path.startswith(base + "/requests/") or path.startswith(base + "/results/"):
                    paths.add(path)
        snapshot = {path: reader.read_bytes(path) for path in sorted(paths)}
    except (OSError, KeyError, ValueError) as exc:
        raise SuccessorRefused("requires_separate_failure_review") from exc
    if snapshot[schema.PLAN_FILE] != plan_bytes:
        _fail("requires_separate_failure_review")
    return files, snapshot, plan


def _require_stable_snapshot(reader: Any, files: set[str], snapshot: dict[str, bytes]) -> None:
    try:
        if set(reader.list_files()) != files:
            _fail("requires_separate_failure_review")
        if any(reader.read_bytes(path) != payload for path, payload in snapshot.items()):
            _fail("requires_separate_failure_review")
    except SuccessorRefused:
        raise
    except (OSError, KeyError, ValueError) as exc:
        raise SuccessorRefused("requires_separate_failure_review") from exc


def _decision_order(
    snapshot: dict[str, bytes], plan: dict[str, Any], bootstrap_seal: dict[str, Any]
) -> None:
    previous_available: int | None = None
    for phase in ("bootstrap", "frame"):
        bucket = plan["schedule"][phase]["bucket"]
        if phase == "frame":
            previous_available = bootstrap_seal["sealed_ns"]
        base = f"buckets/{bucket}"
        request_paths = sorted(path for path in snapshot if path.startswith(base + "/requests/"))
        for request_path in request_paths:
            ordinal = request_path.rsplit("/", 1)[1].removesuffix(".json")
            result_path = f"{base}/results/{ordinal}.json"
            try:
                request = verifier.decode(snapshot[request_path])
                result = verifier.decode(snapshot[result_path])
                decision_ns = request["decision_ns"]
                available_ns = result["availability_ns"]
            except (KeyError, TypeError, ValueError) as exc:
                raise SuccessorRefused("requires_separate_failure_review") from exc
            if type(decision_ns) is not int or (
                previous_available is not None and decision_ns < previous_available
            ):
                _fail("acquisition_decision_before_prior_availability")
            if type(available_ns) is not int:
                _fail("requires_separate_failure_review")
            previous_available = available_ns


def compile_candidate(
    reader: Any,
    *,
    expected_plan_sha256: str,
    expected_root_id: str,
    expected_volume_uuid: str,
    proposed_service_id: str,
    proposed_bootstrap_start_ns: int,
    proposed_frame_start_ns: int,
    now_ns: int,
) -> dict[str, Any]:
    """Return an offline successor candidate after independently verifying v1 evidence.

    The root/volume values and ``now_ns`` are caller pins for compilation only.
    They are not live mount or trusted-clock evidence. A separate v2 runtime
    review must re-establish those facts before any activation. The caller must
    hold host/storage/service read quiescence while compiling from a real root;
    double snapshots catch ordinary concurrent changes but cannot prove atomicity
    against a malicious writer.
    """
    if (
        not isinstance(expected_plan_sha256, str)
        or len(expected_plan_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_plan_sha256)
    ):
        _fail("stale_plan_pin")
    if not isinstance(expected_root_id, str) or not expected_root_id:
        _fail("storage_identity_pin_invalid")
    if not isinstance(expected_volume_uuid, str) or not expected_volume_uuid:
        _fail("storage_identity_pin_invalid")
    if not isinstance(proposed_service_id, str) or not _SERVICE_ID.fullmatch(proposed_service_id):
        _fail("service_id_invalid")
    if not all(
        _integer(value) for value in (proposed_bootstrap_start_ns, proposed_frame_start_ns, now_ns)
    ):
        _fail("schedule_time_invalid")

    try:
        files_before, snapshot, plan = _snapshot_before_verify(reader)
        state = verifier.verify(
            reader,
            expected_plan_sha256=expected_plan_sha256,
            expected_sources=engine.source_bindings(),
        )
        _require_stable_snapshot(reader, files_before, snapshot)
        event_bytes = snapshot[schema.EVENTS_FILE]
        events = verifier.verify_events(event_bytes)
    except verifier.VerificationError as exc:
        reason = (
            "stale_plan_pin"
            if str(exc) == "plan_pin_mismatch"
            else "requires_separate_failure_review"
        )
        raise SuccessorRefused(reason) from exc
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SuccessorRefused("requires_separate_failure_review") from exc

    if (
        state.get("status") != "verified_prefix"
        or state.get("stop") is not None
        or state.get("activation_authorized") is not False
        or state.get("dispatch_authorized") is not False
        or state.get("replay_authorized") is not False
    ):
        _fail("requires_separate_failure_review")

    storage = plan["storage"]
    if (
        storage.get("root_id") != expected_root_id
        or storage.get("volume_uuid") != expected_volume_uuid
    ):
        _fail("storage_identity_pin_mismatch")
    service_id = plan["service_id"]
    if proposed_service_id == service_id:
        _fail("service_id_reuse")

    schedule = plan["schedule"]
    bootstrap_bucket = schedule["bootstrap"]["bucket"]
    frame_bucket = schedule["frame"]["bucket"]
    if (
        state.get("buckets", {}).get(bootstrap_bucket, {}).get("status") != "sealed"
        or state.get("buckets", {}).get(frame_bucket, {}).get("status") != "sealed"
        or not events
        or events[-1].get("kind") != "seal"
        or events[-1].get("bucket") != frame_bucket
    ):
        _fail("requires_separate_failure_review")

    bootstrap_seal = verifier.decode(snapshot[f"buckets/{bootstrap_bucket}/seal.json"])
    frame_bundle = snapshot[f"buckets/{frame_bucket}/bundle.json"]
    bootstrap_bundle = snapshot[f"buckets/{bootstrap_bucket}/bundle.json"]
    if (
        verifier.sha(bootstrap_bundle) != state["buckets"][bootstrap_bucket]["bundle_sha256"]
        or verifier.sha(frame_bundle) != state["buckets"][frame_bucket]["bundle_sha256"]
    ):
        _fail("requires_separate_failure_review")
    _decision_order(snapshot, plan, bootstrap_seal)

    expires_ns = schedule["expires_ns"]
    frame_seal = verifier.decode(snapshot[f"buckets/{frame_bucket}/seal.json"])
    terminal_ns = max(expires_ns, frame_seal["sealed_ns"], events[-1]["at_ns"])
    if now_ns < terminal_ns:
        _fail("predecessor_not_expired")
    earliest_ns = max(now_ns, terminal_ns) + _MINUTE_NS
    if proposed_bootstrap_start_ns < earliest_ns:
        _fail("bootstrap_not_future")
    if proposed_bootstrap_start_ns % _MINUTE_NS or proposed_frame_start_ns % _MINUTE_NS:
        _fail("schedule_not_utc_minute_aligned")
    frame_delta = proposed_frame_start_ns - proposed_bootstrap_start_ns
    if not 5 * _MINUTE_NS <= frame_delta <= 10 * _MINUTE_NS:
        _fail("schedule_spacing_invalid")

    bootstrap_name = _bucket(proposed_bootstrap_start_ns)
    frame_name = _bucket(proposed_frame_start_ns)
    if (
        bootstrap_name == frame_name
        or bootstrap_name in {bootstrap_bucket, frame_bucket}
        or frame_name in {bootstrap_bucket, frame_bucket}
    ):
        _fail("bucket_identity_reuse")

    predecessor = {
        "kind": "external-v1-sealed",
        "service_id": service_id,
        "plan_sha256": state["plan_sha256"],
        "source_sha256": dict(plan["code_sha256"]),
        "storage": dict(storage),
        "event_tail_sha256": events[-1]["event_sha256"],
        "events_file_sha256": verifier.sha(event_bytes),
        "bootstrap": {
            "bucket": bootstrap_bucket,
            "bundle_sha256": verifier.sha(bootstrap_bundle),
            "seal_sha256": verifier.sha(snapshot[f"buckets/{bootstrap_bucket}/seal.json"]),
        },
        "frame": {
            "bucket": frame_bucket,
            "bundle_sha256": verifier.sha(frame_bundle),
            "seal_sha256": verifier.sha(snapshot[f"buckets/{frame_bucket}/seal.json"]),
        },
        "frontier": state["frontier"],
        "gap_intervals": state["gap_intervals"],
        "requests": state["requests"],
        "raw_bytes": state["raw_bytes"],
        "stored_bytes": state["stored_bytes"],
        "expires_ns": expires_ns,
        "terminal_sealed_ns": frame_seal["sealed_ns"],
    }
    proposal = {
        "service_id": proposed_service_id,
        "storage": {
            "root_id": storage["root_id"],
            "volume_uuid": storage["volume_uuid"],
            "relative_root": f"services/{proposed_service_id}",
        },
        "schedule": {
            "bootstrap": {
                "bucket": bootstrap_name,
                "start_ns": proposed_bootstrap_start_ns,
                "end_ns": proposed_bootstrap_start_ns + 60 * _NS,
            },
            "frame": {
                "bucket": frame_name,
                "start_ns": proposed_frame_start_ns,
                "end_ns": proposed_frame_start_ns + 180 * _NS,
            },
            "expires_ns": proposed_frame_start_ns + 180 * _NS,
        },
        "caps": dict(plan["caps"]),
    }
    return {
        "schema": "brain-senses-external-successor-v2-candidate",
        "schema_version": 2,
        "predecessor": predecessor,
        "predecessor_sha256": verifier.sha(verifier.canonical(predecessor)),
        "proposal": proposal,
        "activation_authorized": False,
        "dispatch_authorized": False,
        "replay_authorized": False,
        "provider_io": False,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
