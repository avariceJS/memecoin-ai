#!/usr/bin/env python3
"""Pinned synthetic activation and journal fixtures for the v3 launch rehearsal.

This fixture contract is intentionally not a production activation format. The
two literal digests are review pins: changing either fixture requires a new
fixture version and review. The fixture proves only local control flow.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from tools.brain_senses_external_launch_v3 import JournalSnapshot, LaunchV3Error, PlanBinding

SCHEMA = "brain-senses-launch-v3-synthetic-activation-envelope-v1"
SERVICE_ID = "launch-v3-synthetic-rehearsal"
BUCKET = "20261012T0000Z"
WINDOW_START_NS = 1_791_763_200_000_000_000
WINDOW_END_NS = WINDOW_START_NS + 86_400_000_000_000
SOURCE_SHA256 = "0" * 64
PREDECESSOR_SHA256 = "1" * 64
PLAN_BYTES = b"synthetic launch-v3 rehearsal plan v1\n"
PLAN_SHA256 = hashlib.sha256(PLAN_BYTES).hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


ACTIVATION_BYTES = _canonical(
    {
        "schema": SCHEMA,
        "schema_version": 1,
        "synthetic": True,
        "service_id": SERVICE_ID,
        "plan_sha256": PLAN_SHA256,
        "scheduled_bucket": BUCKET,
        "window_start_ns": WINDOW_START_NS,
        "window_end_ns": WINDOW_END_NS,
        "source_sha256": SOURCE_SHA256,
        "predecessor_certificate_sha256": PREDECESSOR_SHA256,
    }
)
# Literal pin is populated from the exact canonical fixture bytes.
ACTIVATION_SHA256 = "c1b31fcca23c028e9ce893081f69acf11cad59bc9e549ce7bd42e03cb1b5635c"

JOURNAL_BYTES = _canonical(
    {
        "schema": "brain-senses-launch-v3-synthetic-service-journal-v1",
        "schema_version": 1,
        "synthetic": True,
        "service_id": SERVICE_ID,
        "plan_sha256": PLAN_SHA256,
        "source_sha256": SOURCE_SHA256,
        "predecessor_certificate_sha256": PREDECESSOR_SHA256,
        "bucket": BUCKET,
        "event_tail_sha256": "2" * 64,
        "reservation_ids": [],
        "intent_ids": [],
    }
)
JOURNAL_SHA256 = "3141a33ddf12f52828e440f5a5f58b7a71e1617aaa4b6145961bc4e5bd094f9d"


def validate_activation(raw: bytes, callback_start_utc_ns: int) -> PlanBinding:
    """Authenticate the pinned synthetic envelope, then bind exact plan bytes."""
    if hashlib.sha256(raw).hexdigest() != ACTIVATION_SHA256 or raw != ACTIVATION_BYTES:
        raise LaunchV3Error("synthetic_activation_untrusted")
    if type(callback_start_utc_ns) is not int or not (
        WINDOW_START_NS <= callback_start_utc_ns < WINDOW_END_NS
    ):
        raise LaunchV3Error("callback_outside_synthetic_window")
    expected_bucket = datetime.fromtimestamp(WINDOW_START_NS // 1_000_000_000, UTC).strftime(
        "%Y%m%dT%H%MZ"
    )
    if expected_bucket != BUCKET:
        raise LaunchV3Error("synthetic_bucket_mismatch")
    return PlanBinding(
        service_id=SERVICE_ID,
        plan_sha256=PLAN_SHA256,
        scheduled_bucket=BUCKET,
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
        source_sha256=SOURCE_SHA256,
        predecessor_certificate_sha256=PREDECESSOR_SHA256,
    )


def validate_synthetic_plan(plan_bytes: bytes, callback_start_utc_ns: int) -> PlanBinding:
    """Verify the source-pinned activation envelope and exact plan as separate inputs."""
    binding = validate_activation(ACTIVATION_BYTES, callback_start_utc_ns)
    if plan_bytes != PLAN_BYTES:
        raise LaunchV3Error("synthetic_plan_untrusted")
    return binding


def verify_journal(raw: bytes, binding: PlanBinding) -> JournalSnapshot:
    """Verify the immutable, empty synthetic journal against its literal pin."""
    if hashlib.sha256(raw).hexdigest() != JOURNAL_SHA256 or raw != JOURNAL_BYTES:
        raise LaunchV3Error("synthetic_journal_untrusted")
    value = json.loads(raw)
    if (
        value.get("synthetic") is not True
        or value.get("service_id") != binding.service_id
        or value.get("plan_sha256") != binding.plan_sha256
        or value.get("source_sha256") != binding.source_sha256
        or value.get("predecessor_certificate_sha256") != binding.predecessor_certificate_sha256
        or value.get("bucket") != binding.scheduled_bucket
    ):
        raise LaunchV3Error("synthetic_journal_binding")
    return JournalSnapshot(
        verified=True,
        service_id=binding.service_id,
        plan_sha256=binding.plan_sha256,
        source_sha256=binding.source_sha256,
        predecessor_certificate_sha256=binding.predecessor_certificate_sha256,
        bucket=binding.scheduled_bucket,
        event_tail_sha256=value["event_tail_sha256"],
        reservation_ids=tuple(value["reservation_ids"]),
        intent_ids=tuple(value["intent_ids"]),
    )
