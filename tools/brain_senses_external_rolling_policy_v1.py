#!/usr/bin/env python3
"""Policy math for independent forward-only Senses acquisition epochs.

This module is deliberately data-only. It does not authorize provider access,
create a storage root, or activate a LaunchAgent. The runtime must bind these
values to its immutable plan and independently verified journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SECOND_NS = 1_000_000_000
MINUTE_NS = 60 * SECOND_NS
HOUR_NS = 60 * MINUTE_NS
DAY_NS = 24 * HOUR_NS
ROLLING_WINDOW_NS = 30 * DAY_NS

MAX_EPOCH_HOURS = 24
MAX_REQUESTS_PER_FRAME = 34
MAX_EPOCH_REQUESTS = 1 + MAX_EPOCH_HOURS * MAX_REQUESTS_PER_FRAME
MAX_RESPONSE_BYTES = 32_000_000
MAX_EPOCH_STORED_BYTES = 4_000_000_000
MAX_ROLLING_REQUESTS = 25_000
MAX_ROLLING_CREDITS = 250_000
MAX_ROLLING_STORED_BYTES = 120_000_000_000
CONSERVATIVE_CREDITS_PER_REQUEST = 10
MAX_REQUESTS_PER_SECOND = 4
DISK_FLOOR_BYTES = 50 * 2**30
PEAK_WRITE_MARGIN_BYTES = 290_000_000
METADATA_MARGIN_BYTES = 32_000_000
WINDOW_WIDTH_NS = 5 * MINUTE_NS

POLICY_ID = "helius-finalized-inventory-hourly-rolling-v1"


class PolicyError(ValueError):
    """Invalid rolling policy value or schedule."""


@dataclass(frozen=True)
class Window:
    """A single forward-only UTC collection opportunity."""

    kind: str
    index: int | None
    bucket: str
    start_ns: int
    end_ns: int
    request_cap: int


@dataclass(frozen=True)
class RollingUsage:
    """Conservative provider and storage use measured from immutable receipts."""

    requests: int
    modeled_credits: int
    stored_bytes: int


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def next_fresh_anchor_ns(now_ns: int, *, minimum_lead_ns: int = 2 * MINUTE_NS) -> int:
    """Return the next UTC-hour boundary with enough prospective lead time."""
    if not _integer(now_ns, minimum=1) or not _integer(minimum_lead_ns, minimum=1):
        raise PolicyError("anchor_time_invalid")
    candidate = ((now_ns + HOUR_NS - 1) // HOUR_NS) * HOUR_NS
    if candidate < now_ns + minimum_lead_ns:
        candidate += HOUR_NS
    return candidate


def _bucket(start_ns: int) -> str:
    try:
        return datetime.fromtimestamp(start_ns // SECOND_NS, UTC).strftime("%Y%m%dT%H0000Z")
    except (OverflowError, OSError, ValueError) as exc:
        raise PolicyError("epoch_time_out_of_range") from exc


def build_epoch(anchor_ns: int) -> tuple[Window, ...]:
    """Build one anchor plus 24 hourly frames; no window can precede the anchor."""
    if not _integer(anchor_ns, minimum=1) or anchor_ns % HOUR_NS:
        raise PolicyError("anchor_alignment_invalid")
    rows = [
        Window(
            kind="anchor",
            index=None,
            bucket=_bucket(anchor_ns),
            start_ns=anchor_ns,
            end_ns=anchor_ns + WINDOW_WIDTH_NS,
            request_cap=1,
        )
    ]
    for index in range(MAX_EPOCH_HOURS):
        start = anchor_ns + (index + 1) * HOUR_NS
        rows.append(
            Window(
                kind="frame",
                index=index,
                bucket=_bucket(start),
                start_ns=start,
                end_ns=start + WINDOW_WIDTH_NS,
                request_cap=MAX_REQUESTS_PER_FRAME,
            )
        )
    if len({row.bucket for row in rows}) != len(rows):
        raise PolicyError("epoch_bucket_collision")
    if rows[-1].end_ns > anchor_ns + DAY_NS + WINDOW_WIDTH_NS:
        raise PolicyError("epoch_horizon_exceeded")
    return tuple(rows)


def disposition(window: Window, now_ns: int) -> str:
    """Classify a poll without ever making an expired window dispatchable."""
    if not _integer(now_ns, minimum=0):
        raise PolicyError("poll_time_invalid")
    if now_ns < window.start_ns:
        return "not_due"
    if now_ns >= window.end_ns:
        return "expired_unobserved"
    return "due"


def request_admissible(
    usage: RollingUsage,
    *,
    epoch_requests: int,
    epoch_stored_bytes: int,
    now_ns: int,
    response_reservation_bytes: int = MAX_RESPONSE_BYTES,
) -> bool:
    """Apply bounded per-request reservations without requiring a billing meter."""
    values = (usage.requests, usage.modeled_credits, usage.stored_bytes)
    if not all(_integer(value) for value in values):
        raise PolicyError("rolling_usage_invalid")
    if not _integer(epoch_requests) or not _integer(epoch_stored_bytes):
        raise PolicyError("epoch_usage_invalid")
    if not _integer(now_ns, minimum=1) or not _integer(response_reservation_bytes, minimum=1):
        raise PolicyError("reservation_invalid")
    return (
        epoch_requests < MAX_EPOCH_REQUESTS
        and usage.requests < MAX_ROLLING_REQUESTS
        and usage.modeled_credits + CONSERVATIVE_CREDITS_PER_REQUEST <= MAX_ROLLING_CREDITS
        and usage.stored_bytes + response_reservation_bytes <= MAX_ROLLING_STORED_BYTES
        and epoch_stored_bytes + response_reservation_bytes <= MAX_EPOCH_STORED_BYTES
    )


def conservative_rolling_usage(intents: list[dict[str, Any]], *, now_ns: int) -> RollingUsage:
    """Count intents in the trailing 30d window; missing charges stay modeled, not exact."""
    if not _integer(now_ns, minimum=1) or not isinstance(intents, list):
        raise PolicyError("rolling_intents_invalid")
    cutoff = now_ns - ROLLING_WINDOW_NS
    requests = credits = stored = 0
    for row in intents:
        if not isinstance(row, dict):
            raise PolicyError("rolling_intent_invalid")
        at_ns = row.get("decision_ns")
        if not _integer(at_ns, minimum=1):
            raise PolicyError("rolling_intent_time_invalid")
        assert type(at_ns) is int
        if cutoff <= at_ns <= now_ns:
            requests += 1
            credits += CONSERVATIVE_CREDITS_PER_REQUEST
            size = row.get("stored_response_bytes")
            if size is None:
                # An intent without a conclusive result reserves one maximum response.
                stored += MAX_RESPONSE_BYTES
            else:
                if not _integer(size) or size > MAX_RESPONSE_BYTES:
                    raise PolicyError("rolling_intent_bytes_invalid")
                assert type(size) is int
                stored += size
    return RollingUsage(requests=requests, modeled_credits=credits, stored_bytes=stored)
