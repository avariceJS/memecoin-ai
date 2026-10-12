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
MAX_SELECTED_SLOTS_PER_FRAME = 16
MAX_INVENTORY_SLOTS_PER_FRAME = 20_000
MAX_EPOCH_REQUESTS = 1 + MAX_EPOCH_HOURS * MAX_REQUESTS_PER_FRAME
MAX_RESPONSE_BYTES = 32_000_000
MAX_EPOCH_RESPONSE_BYTES = 4_000_000_000
MAX_EPOCH_PHYSICAL_BYTES = 4_000_000_000
MAX_ROLLING_REQUESTS = 25_000
MAX_ROLLING_CREDITS = 250_000
MAX_ROLLING_RESPONSE_BYTES = 120_000_000_000
MAX_ROLLING_PHYSICAL_BYTES = 120_000_000_000
MAX_SERVICE_LIFETIME_ALLOCATION_BYTES = 150_000_000_000
CONSERVATIVE_CREDITS_PER_REQUEST = 10
MAX_REQUESTS_PER_SECOND = 4
DISK_FLOOR_BYTES = 50 * 2**30
PEAK_WRITE_MARGIN_BYTES = 290_000_000
METADATA_MARGIN_BYTES = 32_000_000
PER_REQUEST_METADATA_RESERVE_BYTES = 128_000
SERVICE_OPERATING_RESERVE_BYTES = 8 * 2**30
NEXT_EPOCH_ALLOCATION_RESERVE_BYTES = MAX_EPOCH_PHYSICAL_BYTES
SERVICE_PEAK_METADATA_RESERVE_BYTES = 322_000_000
WINDOW_WIDTH_NS = 5 * MINUTE_NS

POLICY_ID = "helius-finalized-inventory-hourly-rolling-v1"


class PolicyError(ValueError):
    """Invalid rolling policy value or schedule."""


def activation_allocation_cap(available_bytes: int) -> int:
    """Pin a service lifetime cap while reserving the disk floor and next epoch."""
    if type(available_bytes) is not int or available_bytes <= 0:
        raise PolicyError("activation_capacity_invalid")
    headroom = (
        available_bytes
        - DISK_FLOOR_BYTES
        - SERVICE_OPERATING_RESERVE_BYTES
        - NEXT_EPOCH_ALLOCATION_RESERVE_BYTES
        - SERVICE_PEAK_METADATA_RESERVE_BYTES
    )
    if headroom <= 0:
        raise PolicyError("activation_capacity_insufficient")
    return min(MAX_SERVICE_LIFETIME_ALLOCATION_BYTES, headroom)


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
    """Conservative provider, response, and allocated storage use from receipts."""

    requests: int
    modeled_credits: int
    response_bytes: int
    allocated_bytes: int


def policy_document() -> dict[str, Any]:
    """Return the canonical immutable contract stored at the Kingston service root."""
    return {
        "schema": "brain-senses-external-rolling-policy-v1",
        "policy_id": POLICY_ID,
        "provider_alias": "helius-free",
        "endpoint_origin": "https://mainnet.helius-rpc.com",
        "epoch_hours": MAX_EPOCH_HOURS,
        "requests_per_frame": MAX_REQUESTS_PER_FRAME,
        "selected_slots_per_frame": MAX_SELECTED_SLOTS_PER_FRAME,
        "max_inventory_slots_per_frame": MAX_INVENTORY_SLOTS_PER_FRAME,
        "selection_rule": "sha256-rank-v1",
        "max_epoch_requests": MAX_EPOCH_REQUESTS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_epoch_response_bytes": MAX_EPOCH_RESPONSE_BYTES,
        "max_epoch_physical_bytes": MAX_EPOCH_PHYSICAL_BYTES,
        "max_rolling_requests": MAX_ROLLING_REQUESTS,
        "max_rolling_modeled_credits": MAX_ROLLING_CREDITS,
        "max_rolling_response_bytes": MAX_ROLLING_RESPONSE_BYTES,
        "max_rolling_physical_bytes": MAX_ROLLING_PHYSICAL_BYTES,
        "max_service_lifetime_allocation_bytes": MAX_SERVICE_LIFETIME_ALLOCATION_BYTES,
        "service_operating_reserve_bytes": SERVICE_OPERATING_RESERVE_BYTES,
        "next_epoch_allocation_reserve_bytes": NEXT_EPOCH_ALLOCATION_RESERVE_BYTES,
        "service_peak_metadata_reserve_bytes": SERVICE_PEAK_METADATA_RESERVE_BYTES,
        "rolling_window_ns": ROLLING_WINDOW_NS,
        "conservative_credits_per_attempt": CONSERVATIVE_CREDITS_PER_REQUEST,
        "max_requests_per_second": MAX_REQUESTS_PER_SECOND,
        "disk_floor_bytes": DISK_FLOOR_BYTES,
        "peak_write_margin_bytes": PEAK_WRITE_MARGIN_BYTES,
        "metadata_margin_bytes": METADATA_MARGIN_BYTES,
        "per_request_metadata_reserve_bytes": PER_REQUEST_METADATA_RESERVE_BYTES,
        "window_width_ns": WINDOW_WIDTH_NS,
        "schedule": "one_fresh_finalized_anchor_then_24_hourly_frames",
        "missed_window": "expired_unobserved_no_backfill",
        "ambiguous_request": "possibly_spent_no_replay",
        "budget_pause": "automatic_until_authenticated_receipt_usage_has_headroom",
        "maximum_abandoned_recovery_epochs_per_rolling_day": 2,
        "epoch_expiry": None,
        "service_lifetime_cap": "pinned_at_first_activation_no_automatic_reset",
        "continuity_claim": False,
    }


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
    epoch_response_bytes: int,
    epoch_allocated_bytes: int,
    now_ns: int,
    response_reservation_bytes: int = MAX_RESPONSE_BYTES,
    physical_write_reservation_bytes: int = MAX_RESPONSE_BYTES + PER_REQUEST_METADATA_RESERVE_BYTES,
) -> bool:
    """Apply response and physical-allocation reservations without a billing meter."""
    values = (
        usage.requests,
        usage.modeled_credits,
        usage.response_bytes,
        usage.allocated_bytes,
    )
    if not all(_integer(value) for value in values):
        raise PolicyError("rolling_usage_invalid")
    if not _integer(epoch_requests) or not _integer(epoch_response_bytes):
        raise PolicyError("epoch_usage_invalid")
    if not _integer(epoch_allocated_bytes):
        raise PolicyError("epoch_allocation_invalid")
    if not _integer(now_ns, minimum=1) or not _integer(response_reservation_bytes, minimum=1):
        raise PolicyError("reservation_invalid")
    if not _integer(physical_write_reservation_bytes, minimum=1):
        raise PolicyError("physical_reservation_invalid")
    return (
        epoch_requests < MAX_EPOCH_REQUESTS
        and usage.requests < MAX_ROLLING_REQUESTS
        and usage.modeled_credits + CONSERVATIVE_CREDITS_PER_REQUEST <= MAX_ROLLING_CREDITS
        and usage.response_bytes + response_reservation_bytes <= MAX_ROLLING_RESPONSE_BYTES
        and usage.allocated_bytes + physical_write_reservation_bytes <= MAX_ROLLING_PHYSICAL_BYTES
        and epoch_response_bytes + response_reservation_bytes <= MAX_EPOCH_RESPONSE_BYTES
        and epoch_allocated_bytes + physical_write_reservation_bytes <= MAX_EPOCH_PHYSICAL_BYTES
    )


def blocked_reason(
    usage: RollingUsage,
    *,
    epoch_requests: int,
    epoch_response_bytes: int,
    epoch_allocated_bytes: int,
) -> str | None:
    """Return the first finite request/response/allocation cap that blocks dispatch."""
    if epoch_requests >= MAX_EPOCH_REQUESTS:
        return "epoch_request_cap"
    if epoch_response_bytes + MAX_RESPONSE_BYTES > MAX_EPOCH_RESPONSE_BYTES:
        return "epoch_response_cap"
    if (
        epoch_allocated_bytes + MAX_RESPONSE_BYTES + PER_REQUEST_METADATA_RESERVE_BYTES
        > MAX_EPOCH_PHYSICAL_BYTES
    ):
        return "epoch_physical_cap"
    if usage.requests >= MAX_ROLLING_REQUESTS:
        return "rolling_request_cap"
    if usage.modeled_credits + CONSERVATIVE_CREDITS_PER_REQUEST > MAX_ROLLING_CREDITS:
        return "rolling_credit_cap"
    if usage.response_bytes + MAX_RESPONSE_BYTES > MAX_ROLLING_RESPONSE_BYTES:
        return "rolling_response_cap"
    if (
        usage.allocated_bytes + MAX_RESPONSE_BYTES + PER_REQUEST_METADATA_RESERVE_BYTES
        > MAX_ROLLING_PHYSICAL_BYTES
    ):
        return "rolling_physical_cap"
    return None


def conservative_rolling_usage(intents: list[dict[str, Any]], *, now_ns: int) -> RollingUsage:
    """Count intents in the trailing 30d window; missing charges stay modeled, not exact."""
    if not _integer(now_ns, minimum=1) or not isinstance(intents, list):
        raise PolicyError("rolling_intents_invalid")
    cutoff = now_ns - ROLLING_WINDOW_NS
    requests = credits = response_bytes = allocated_bytes = 0
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
            size = row.get("response_bytes")
            if size is None:
                # An intent without a conclusive result reserves one maximum response.
                response_bytes += MAX_RESPONSE_BYTES
            else:
                if not _integer(size) or size > MAX_RESPONSE_BYTES:
                    raise PolicyError("rolling_intent_bytes_invalid")
                assert type(size) is int
                response_bytes += size
            allocation = row.get("allocated_bytes")
            if allocation is None:
                # Includes one max response plus request, receipt, result, and journal overhead.
                allocated_bytes += MAX_RESPONSE_BYTES + PER_REQUEST_METADATA_RESERVE_BYTES
            else:
                if not _integer(allocation) or allocation > (
                    MAX_RESPONSE_BYTES + PER_REQUEST_METADATA_RESERVE_BYTES
                ):
                    raise PolicyError("rolling_intent_allocation_invalid")
                assert type(allocation) is int
                allocated_bytes += allocation
    return RollingUsage(
        requests=requests,
        modeled_credits=credits,
        response_bytes=response_bytes,
        allocated_bytes=allocated_bytes,
    )
